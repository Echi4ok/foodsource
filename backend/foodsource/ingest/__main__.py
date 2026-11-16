import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scrapy import Spider, signals
from scrapy.crawler import CrawlerProcess

from foodsource.domain import CatalogSnapshot
from foodsource.repository import (
    DEFAULT_DB_PATH,
    CatalogNotPublished,
    import_catalog,
    load_catalog_revision,
)

from .expocentr import ExpocentrSpider
from .fsa import ARCHIVES as DEFAULT_DECLARATION_ARCHIVES
from .fsa import FsaDeclarationsSpider
from .geography import Geography, load_geography
from .mvk import MvkSpider
from .normalize import normalized_snapshot, resolve_observation_locations
from .producers import PROVIDER as PRODUCER_SITES
from .producers import ProducerSitesSpider
from .refresh import preserve_temporarily_unavailable
from .regional import RegionalSpider
from .regional_catalogs import PrimoryeExportSpider, SiberianFoodWeekSpider
from .rosupack import RosUpackSpider

ITEM_ERRORS = "foodsource/item_errors"


def count_item_error(spider: Spider) -> None:
    if spider.crawler.stats:
        spider.crawler.stats.inc_value(ITEM_ERRORS)


def crawl(
    *,
    log_level: str,
    with_overture: bool,
    geography: Geography | None = None,
    overture_cities: tuple[str, ...] = (),
    declaration_archives: int = DEFAULT_DECLARATION_ARCHIVES,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    providers = [
        (ExpocentrSpider, {"exhibitions": ("prodexpo", "agroprodmash")}),
        (RegionalSpider, {}),
        (MvkSpider, {"city": None, "region_id": None}),
        (ProducerSitesSpider, {}),
        (SiberianFoodWeekSpider, {}),
        (PrimoryeExportSpider, {}),
        (FsaDeclarationsSpider, {"archive_count": declaration_archives}),
        (RosUpackSpider, {}),
    ]
    if with_overture:
        from .overture import DEFAULT_CITIES, OvertureSpider

        providers.append(
            (OvertureSpider, {"cities": overture_cities or DEFAULT_CITIES, "geography": geography})
        )
    return collect_providers(providers, log_level=log_level)


def collect_providers(
    providers: list[tuple[type[Spider], dict[str, Any]]], *, log_level: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    process = CrawlerProcess(
        {
            "BOT_NAME": "FoodSource",
            "USER_AGENT": "FoodSourceCatalog/1.0",
            "ROBOTSTXT_OBEY": True,
            "CONCURRENT_REQUESTS": 8,
            "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
            "DOWNLOAD_DELAY": 0.35,
            "DOWNLOAD_TIMEOUT": 16,
            "DOWNLOAD_MAXSIZE": 6_000_000,
            "RETRY_TIMES": 2,
            "AUTOTHROTTLE_ENABLED": True,
            "AUTOTHROTTLE_START_DELAY": 0.35,
            "AUTOTHROTTLE_MAX_DELAY": 4,
            "COOKIES_ENABLED": False,
            "MEMUSAGE_ENABLED": False,
            "TELNETCONSOLE_ENABLED": False,
            "EXTENSIONS": {"scrapy.extensions.remote_control.RemoteControl": None},
            "ITEM_PIPELINES": {"foodsource.ingest.enrich.OfficialWebsitePipeline": 300},
            "LOG_LEVEL": log_level,
        }
    )
    observations: list[dict[str, Any]] = []
    crawlers = []

    def capture(item: dict[str, Any]) -> None:
        observations.append(dict(item))

    for provider, kwargs in providers:
        crawler = process.create_crawler(provider)
        crawler.signals.connect(capture, signal=signals.item_scraped, weak=False)
        crawler.signals.connect(count_item_error, signal=signals.item_error)
        crawlers.append(crawler)
        process.crawl(crawler, **kwargs)
    process.start()

    stats = {
        crawler.spidercls.name: crawler.stats.get_stats() if crawler.stats else {}
        for crawler in crawlers
    }
    producer_stats = stats.get(PRODUCER_SITES, {})
    if (
        producer_stats.get("finish_reason") != "finished"
        or producer_stats.get("spider_exceptions/count")
        or producer_stats.get(ITEM_ERRORS)
    ):
        observations = [item for item in observations if item.get("provider") != PRODUCER_SITES]
    return observations, stats


def ensure_completed(stats: dict[str, dict[str, Any]]) -> None:
    invalid = []
    for provider, snapshot in stats.items():
        if provider == PRODUCER_SITES:
            continue
        errors = sum(
            snapshot.get(key, 0)
            for key in ("spider_exceptions/count", "foodsource/source_errors", ITEM_ERRORS)
        )
        count = snapshot.get("item_scraped_count", 0)
        if (
            snapshot.get("finish_reason") != "finished"
            or errors
            or (not count and provider != "overture")
        ):
            invalid.append(
                f"{provider}: {snapshot.get('finish_reason', 'not started')}, "
                f"{errors} errors, {count} observations"
            )
    if invalid:
        raise RuntimeError("Catalog import incomplete: " + "; ".join(invalid))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the Russian supplier catalog")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite catalog path")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate sources without publishing"
    )
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING"], default="INFO")
    parser.add_argument(
        "--with-overture", action="store_true", help="Add the open Overture Maps Places source"
    )
    parser.add_argument(
        "--overture-city",
        action="append",
        default=[],
        help="City to check in Overture Maps; repeat as needed",
    )
    parser.add_argument(
        "--declaration-archives",
        type=int,
        default=DEFAULT_DECLARATION_ARCHIVES,
        help="Number of recent monthly declaration archives to import",
    )
    args = parser.parse_args()
    if args.declaration_archives < 1:
        parser.error("--declaration-archives must be positive")
    try:
        path = args.db.resolve()
        try:
            publication = load_catalog_revision(path)
        except (FileNotFoundError, CatalogNotPublished):
            publication = None
        previous = publication.snapshot if publication is not None else None
        base_revision = publication.revision if publication is not None else 0
        geography = load_geography()
        raw, stats = crawl(
            log_level=args.log_level,
            with_overture=args.with_overture or bool(args.overture_city),
            geography=geography,
            overture_cities=tuple(args.overture_city),
            declaration_archives=args.declaration_archives,
        )
        ensure_completed(stats)
        located, regions, unresolved = resolve_observation_locations(raw, geography)
        observations, retained = preserve_temporarily_unavailable(located, previous)
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        snapshot = CatalogSnapshot.model_validate(normalized_snapshot(observations, today, regions))
        location_ids = {supplier.region_id for supplier in snapshot.suppliers}
        by_provider = Counter(item["provider"] for item in observations)
        no_locations = {ExpocentrSpider.name, FsaDeclarationsSpider.name} - by_provider.keys()
        if no_locations:
            raise RuntimeError(
                f"No geocoded supplier observations from {', '.join(sorted(no_locations))}"
            )
        unlocated_by_provider = Counter(item["provider"] for item in raw) - Counter(
            item["provider"] for item in located
        )
        summary = {
            "locations": len(location_ids),
            "collected": dict(sorted(by_provider.items())),
            "unresolved_location": unresolved.total(),
            "unresolved_location_by_provider": dict(sorted(unlocated_by_provider.items())),
            "unresolved_location_examples": dict(unresolved.most_common(10)),
            "unqualified": len(located) - len(observations),
            "retained_on_network_failure": retained,
            "optional_source_errors": {
                PRODUCER_SITES: sum(
                    stats.get(PRODUCER_SITES, {}).get(key, 0)
                    for key in ("spider_exceptions/count", "foodsource/source_errors", ITEM_ERRORS)
                ),
            },
            "published": not args.dry_run,
        }
        if not args.dry_run:
            result = import_catalog(snapshot, path, expected_revision=base_revision)
            summary.update(
                suppliers=result.supplier_count,
                observations=result.observation_count,
                revision=result.revision,
                changed=result.changed,
            )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    except (RuntimeError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

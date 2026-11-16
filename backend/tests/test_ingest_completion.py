import subprocess
import sys
from pathlib import Path


def test_collector_rejects_partial_crawl_after_item_pipeline_error() -> None:
    probe = """\
from scrapy import Request, Spider

from foodsource.ingest import __main__ as ingest, enrich


class FixtureSpider(Spider):
    name = "expocentr"
    custom_settings = {"ROBOTSTXT_OBEY": False}

    async def start(self):
        yield Request("data:text/html,fixture")

    def parse(self, response):
        return [{"bad": False}, {"bad": self.name == "expocentr"}]


class BrokenOnOneItem:
    def process_item(self, item):
        if item["bad"]:
            raise ValueError("fixture item pipeline failure")
        return item


enrich.OfficialWebsitePipeline = BrokenOnOneItem
Regional = type("Regional", (FixtureSpider,), {"name": "regional"})
Mvk = type("Mvk", (FixtureSpider,), {"name": "mvk"})
Producers = type("Producers", (FixtureSpider,), {
    "name": ingest.PRODUCER_SITES,
    "parse": lambda spider, response: [
        {"provider": ingest.PRODUCER_SITES, "bad": False},
        {"provider": ingest.PRODUCER_SITES, "bad": True},
    ],
})
items, stats = ingest.collect_providers(
    [(spider, {}) for spider in (FixtureSpider, Regional, Mvk, Producers)], log_level="WARNING"
)
assert len(items) == 5, stats
assert stats["expocentr"].get(ingest.ITEM_ERRORS) == 1, stats
assert stats[ingest.PRODUCER_SITES].get(ingest.ITEM_ERRORS) == 1, stats
assert not any(item.get("provider") == ingest.PRODUCER_SITES for item in items)
ingest.ensure_completed({key: value for key, value in stats.items() if key != "expocentr"})
assert stats["expocentr"]["finish_reason"] == "finished", stats
try:
    ingest.ensure_completed(stats)
except RuntimeError as error:
    assert "expocentr: finished, 1 errors" in str(error)
else:
    raise AssertionError("partial source was accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr

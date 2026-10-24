import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources.cbr_rss import MAX_BYTES, SourceAdapter, SourceError, parse

FIX = Path(__file__).parent / "fixtures" / "cbr_rss"


class CbrRssTests(unittest.TestCase):
    def test_happy_path_timezone_html_and_synthetic_provenance(self):
        item, = parse((FIX / "happy.xml").read_bytes(), synthetic=True)
        self.assertEqual(item.published_at, "2025-10-21T07:00:00Z")
        self.assertEqual(item.body_text, "Текст пример")
        self.assertTrue(item.synthetic)
        self.assertEqual(item.provenance, "synthetic_fixture")

    def test_missing_and_invalid_fields_stay_unknown(self):
        item, = parse((FIX / "missing.xml").read_bytes())
        self.assertIsNone(item.published_at)
        self.assertIsNone(item.body_text)
        invalid, = parse((FIX / "invalid_date.xml").read_bytes())
        self.assertEqual(invalid.published_at_raw, "вчера около полудня")
        self.assertIsNone(invalid.published_at)
        self.assertIsNone(invalid.canonical_url)

    def test_duplicate_identity_dedup_and_same_title_distinct_guid(self):
        xml = b'<rss><channel><item><title>same</title><guid>a</guid></item><item><title>same</title><guid>a</guid></item><item><title>same</title><guid>b</guid></item></channel></rss>'
        items = parse(xml)
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0].source_id, items[1].source_id)

    def test_conflict_is_retained(self):
        xml = b'<rss><channel><item><guid>x</guid><title>one</title></item><item><guid>x</guid><title>two</title></item><item><guid>x</guid><title>one</title></item><item><guid>x</guid><title>two</title></item></channel></rss>'
        items = parse(xml)
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item.source_id for item in items}), 2)

    def test_conflicting_variant_ids_do_not_depend_on_item_order(self):
        first = b'<rss><channel><item><guid>x</guid><title>one</title></item><item><guid>x</guid><title>two</title></item></channel></rss>'
        second = b'<rss><channel><item><guid>x</guid><title>two</title></item><item><guid>x</guid><title>one</title></item></channel></rss>'
        ids = lambda data: {item.title: item.source_id for item in parse(data)}
        self.assertEqual(ids(first), ids(second))

    def test_noncanonical_explicit_port_is_rejected(self):
        xml = b'<rss><channel><item><link>https://cbr.ru:444/path</link></item><item><link>https://cbr.ru:443/path</link></item></channel></rss>'
        items = parse(xml)
        self.assertIsNone(items[0].canonical_url)
        self.assertEqual(items[1].canonical_url, "https://cbr.ru/path")

    def test_rejects_encoded_dtd_and_unsafe_urls(self):
        dtd = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE rss [<!ENTITY x "expanded">]><rss><channel/></rss>'.encode("utf-16")
        with self.assertRaises(SourceError) as caught:
            parse(dtd)
        self.assertEqual(caught.exception.code, "unsafe_xml_declaration")
        xml = b'<rss><channel><item><link>https://attacker.example/story</link></item><item><link>https://www.cbr.ru/story?token=secret</link></item><item><link>http://www.cbr.ru/story</link></item></channel></rss>'
        self.assertTrue(all(item.canonical_url is None for item in parse(xml)))

    def test_rejects_bad_xml_declarations_size_depth_and_format(self):
        for data, code in ((b'<rss><', "malformed_xml"), (b'<!DOCTYPE rss [<!ENTITY x "y">]><rss/>', "unsafe_xml_declaration"), (b" " * (MAX_BYTES + 1), "response_too_large"), (b'<html/>', "unsupported_feed_format")):
            with self.subTest(code=code), self.assertRaises(SourceError) as caught:
                parse(data)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(SourceError):
            parse((b"<rss>" + b"<a>" * 35 + b"</a>" * 35 + b"</rss>"))
        many = b"<rss><channel>" + b"<item/>" * 10000 + b"</channel></rss>"
        with self.assertRaises(SourceError) as caught:
            parse(many)
        self.assertEqual(caught.exception.code, "too_many_xml_elements")

    def test_fetch_is_capped_once_since_and_errors_are_explicit(self):
        calls = []
        data = (FIX / "happy.xml").read_bytes()
        def fake(url, timeout, cap):
            calls.append((url, timeout, cap))
            return data
        batch = SourceAdapter(fake).fetch(datetime(2025, 10, 21, 8, tzinfo=timezone.utc))
        self.assertEqual(batch.status, "ok")
        self.assertEqual(len(batch.items), 0)
        self.assertEqual(calls, [("https://www.cbr.ru/rss/RssPress", 12, MAX_BYTES + 1)])
        failed = SourceAdapter(lambda *_: (_ for _ in ()).throw(TimeoutError())).fetch()
        self.assertEqual((failed.status, failed.error), ("error", "network_error"))
        too_big = SourceAdapter(lambda *_: b"x" * (MAX_BYTES + 1)).fetch()
        self.assertEqual(too_big.error, "response_too_large")
        self.assertEqual(SourceAdapter(fake).fetch(cursor="page=2").error, "unsupported_cursor")
        naive = SourceAdapter(fake).fetch(datetime(2025, 10, 21, 8))
        self.assertEqual((naive.status, naive.error), ("error", "invalid_since_timezone"))


if __name__ == "__main__":
    unittest.main()

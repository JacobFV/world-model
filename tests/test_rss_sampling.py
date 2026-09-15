import tempfile
import unittest
from pathlib import Path
from worldmodel.sampling import extract_rows


class RSSSamplingTests(unittest.TestCase):
    def test_metadata_only_and_xml_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'feed.xml'
            path.write_text('<rss><channel><item><title>Test &amp; title</title><link>https://example.test/a</link><description>Unretained body</description></item></channel></rss>')
            rows=list(extract_rows(path,{'format':'rss','max_uncompressed_bytes':1000}))
            self.assertEqual(rows[0]['title'],'Test & title')
            self.assertNotIn('Unretained body',str(rows))
            self.assertEqual(rows[0]['_source'],{'item':1})
            with self.assertRaisesRegex(ValueError,'budget'):
                list(extract_rows(path,{'format':'rss','max_uncompressed_bytes':10}))
            for encoding in ('utf-8','utf-16'):
                path.write_bytes('<!DOCTYPE rss [<!ENTITY e "expanded">]><rss><channel/></rss>'.encode(encoding))
                with self.assertRaisesRegex(ValueError,'DTD'):
                    list(extract_rows(path,{'format':'rss','max_uncompressed_bytes':1000}))

    def test_literal_html_declaration_in_cdata_is_not_xml_dtd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'feed.xml'
            path.write_text('<rss><channel><item><title>Safe</title><description><![CDATA[<!DOCTYPE html><html>unretained</html>]]></description></item></channel></rss>')
            self.assertEqual(list(extract_rows(path,{'format':'rss','max_uncompressed_bytes':1000}))[0]['title'],'Safe')

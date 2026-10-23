import unittest

from src.app.mail_content import clean_mail_html


class MailContentTests(unittest.TestCase):
    def test_keeps_formatting_and_safe_links(self):
        body = '<p><strong>Cześć</strong> &amp; <em>ważne</em></p><ol><li><a href="https://example.com/?a=1&amp;b=2">Plan</a></li></ol>'
        self.assertEqual(clean_mail_html(body), body)

    def test_removes_active_content_and_attributes(self):
        body = '<svg><script>bad()</script></svg><p style="color:red" onmouseover="bad()">Tekst<img src="x" onerror="bad()"></p>'
        self.assertEqual(clean_mail_html(body), '<p style="color: red">Tekst</p>')

    def test_disallows_encoded_script_links(self):
        self.assertEqual(clean_mail_html('<a href="java&#10;script:alert(1)">link</a>'), '<a>link</a>')

    def test_preserves_full_editor_formatting(self):
        body = '<h2 style="text-align: center; color: #ff0000">Tytuł</h2><p><span style="font-family: Arial; font-size: 18pt; background-color: rgb(255, 255, 0)">Treść</span><sub>2</sub></p><table style="border-collapse: collapse; width: 100%"><tbody><tr><td colspan="2" style="border: 1px solid #000">Termin</td></tr></tbody></table>'
        self.assertEqual(clean_mail_html(body), body)

    def test_images_require_public_http_urls(self):
        self.assertEqual(clean_mail_html('<img src="https://example.com/photo.png" alt="Zdjęcie" width="400">'), '<img src="https://example.com/photo.png" alt="Zdjęcie" width="400">')
        for url in ('data:image/svg+xml,bad', 'javascript:bad()', '/relative.png'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                clean_mail_html(f'<img src="{url}">')

    def test_styles_cannot_load_resources_or_execute_code(self):
        body = '<p style="color: red; background-color: url(https://example.com); width: expression(alert(1)); position: fixed; font-family: evil\\73cript">Tekst</p>'
        self.assertEqual(clean_mail_html(body), '<p style="color: red">Tekst</p>')

    def test_anchor_and_blank_link_are_safe(self):
        self.assertEqual(clean_mail_html('<a href="#plan" target="_blank">Plan</a>'), '<a href="#plan" rel="noopener noreferrer" target="_blank">Plan</a>')

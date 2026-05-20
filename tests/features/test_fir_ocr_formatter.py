import unittest

from app.features.documents.ocr_service import (
    _normalize_text,
    _postprocess_ocr_text,
    _render_printable_fir_html,
)


FIR_OCR_SAMPLE = """
FIRST INFORMATION REPORT
( Under Section 173 B.N.S.S ) प्रथम सूचना रिपोर्ट

1. District / Unit ( जिला / इकाई ) : P.S. ( थाना ) : ठाकुरद्वारा Year ( वर्ष ) : 2025
मुरादाबाद
FIR No. ( प्र.सू.रि. सं . ) : 0059
Date and Time of FIR ( प्र.सू.रि. की दिनांक और समय ): 17/02/2025 14:21 घंटे

2. S.No. Acts ( अधिनियम ) Sections ( धारा ( एँ ) )
1 भारतीय न्याय संहिता ( बी एन एस ) , 2023 74
2 भारतीय न्याय संहिता ( बी एन एस ) , 2023 76
3 भारतीय न्याय संहिता ( बी एन एस ) , 2023 115 ( 2 )
4 भारतीय न्याय संहिता ( बी एन एस ) , 2023 351 ( 3 )

3. ( a ) Occurrence of offence ( अपराध की घटना )
1 Day ( दिन ): शनिवार Date from ( दिनांक से ) : 15/02/2025 Date To ( दिनांक तक ): 15/02/2025
Time Period ( समय अवधि ): पहर 7 Time From ( समय से ): 20:00 बजे Time To ( समय तक ) : 20:00 बजे
( b ) Information received at P.S. ( थाना जहां सूचना प्राप्त हुई ): Date ( दिनांक ): 17/02/2025 Time ( समय ) : 14:21 बजे
( c ) General Diary Reference ( रोजनामचा संदर्भ ): Entry No. ( प्रविष्टि सं . ) : 058

4 . Type of Information ( सूचना का प्रकार ): लिखित

5. Place of Occurrence
1. ( a ) Direction and distance from P.S. ( थाना से दूरी और दिशा ) : दक्षिण , 06 कि . मी .
( b ) Address ( पता ) : ग्राम लालापुर पीपलसाना , थाना ठाकुरद्वारा मुरा 0

6 . Complainant / Informant ( शिकायतकर्ता / सूचनाकर्ता )
( a ) Name ( नाम ): खिलराज सिंह
( b ) Father's Name ( पिताका नाम ): लेखराज सिंह
( c ) Date / Year of Birth ( जन्म तिथि / वर्ष ): 1973
( d ) Nationality ( राष्ट्रीयता ): भारत
( i ) Address ( पता )
1 वर्तमान पता ग्राम लालापुर पीपलसाना , ठाकुरद्वारा , मुरादाबाद , उत्तर प्रदेश , भारत
2 स्थायी पता ग्राम लालापुर पीपलसाना , ठाकुरद्वारा , मुरादाबाद , उत्तर प्रदेश , भारत
( j ) Phone number ( दूरभाष सं . ): Mobile ( मोबाइल सं . ) : 91-97197XXXXX

7 . Details of known / suspected / unknown accused with full particulars
Accused More Than ( अज्ञात आरोपी एक से अधिक हों तो संख्या ) : 0
S. No. Name ( नाम ) Alias Relative's Present Address
Address ( वर्तमान हरि सिंह पीपलसाना , ठाकुरद्वारा , मुरादाबाद , उत्तर प्रदेश , भारत

8. Reasons for delay in reporting by the complainant / informant :

9. Particulars of properties of interest :

10. Total value of property ( In Rs / - ) :

11. Inquest Report / U.D. case No. , if any :

12. First Information contents ( प्रथम सूचना तथ्य ):
नकल तहरीर हिन्दी सेवा में . श्रीमान थाना प्रभारी निरीक्षक महोदय थाना ठाकुरद्वारा जनपद मुरादाबाद महोदय , निवेदन इस प्रकार है कि मैं खिलराज सिंह पुत्र लेखराज सिंह निवासी ग्राम लालापुर पीपलसाना थाना ठाकुरद्वारा जनपद मुरादाबाद का निवासी हूँ दिनांक 15/02/2025 को सांय 8.00 बजे मैं और मेरे परिवार की महिलाए अपनी बहन ग्राम गझेडा आलम में भात देने गये थे वहां पर मौजूद योगेश पुत्र हरि सिंह गौरव सिंह पुत्र हरि सिंह व सौरव पुत्र हरि सिंह ग्राम लालापुर पीलसाना थाना ठाकुरद्वारा जिला मुरादाबाद ने उर्मिला पत्नी दलीप सिंह , बबीता पत्नी मुंशीराम व निर्देश देवी पत्नी सत्यप्रकाश सिंह के साथ में छेडछाड की और हाथापाई की कपडे फाड़ दिये और उनसे मना किया तो जान से मारने की धमकी दी

13. Action taken :
( 1 ) Registered the case and took up the investigation : / or
( 2 ) Directed ( Name of I.O. ) ( जांच अधिकारी का नाम ) : mayank partap Rank ( पद ) : उपनिरीक्षक / अवर निरीक्षक No. ( सं . ) :
Name ( नाम ) : THANA THAKURDWARA Rank ( पद ) : I ( Inspector ) No. ( सं . ) : 9454404056

14. Signature / Thumb impression of the complainant / informant

15. Date and time of dispatch to the court :
"""


class FirOcrFormatterTest(unittest.TestCase):
    def render(self) -> str:
        cleaned = _postprocess_ocr_text(_normalize_text(FIR_OCR_SAMPLE))
        html = _render_printable_fir_html(cleaned)
        self.assertIsNotNone(html)
        return html or ""

    def test_basic_details_are_split(self):
        html = self.render()
        self.assertIn("District/Unit", html)
        self.assertIn("मुरादाबाद", html)
        self.assertIn("Police Station", html)
        self.assertIn("ठाकुरद्वारा", html)
        self.assertIn("FIR No.", html)
        self.assertIn("0059", html)
        self.assertIn("17/02/2025 14:21", html)

    def test_sections_table_is_preserved(self):
        html = self.render()
        for section in (
            "<td>74</td>",
            "<td>76</td>",
            "<td>115(2)</td>",
            "<td>351(3)</td>",
        ):
            self.assertIn(section, html)
        self.assertIn("S.No. (क्र.सं.)", html)
        self.assertIn("Acts (अधिनियम)", html)
        self.assertIn("Sections (धारा(एँ))", html)

    def test_accused_are_not_mixed(self):
        html = self.render()
        for name in ("योगेश", "गौरव सिंह", "सौरव"):
            self.assertIn(f"<td>{name}</td>", html)
        self.assertEqual(html.count("पिता का नाम: हरि सिंह"), 3)

    def test_narrative_is_preserved_and_empty_tables_are_quiet(self):
        html = self.render()
        self.assertIn("नकल तहरीर हिन्दी सेवा में", html)
        self.assertIn("जान से मारने की धमकी दी", html)
        self.assertIn("9. Particulars of properties", html)
        self.assertIn("Property Category", html)
        self.assertNotIn("Propertty Category", html)


if __name__ == "__main__":
    unittest.main()

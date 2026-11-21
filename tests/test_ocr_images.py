import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ocr_engine import OcrSettings, process_batch
from ocr_inputs import IMAGE_DPI, inspect_input, open_input
from tests.ocr_fixtures import make_pdf
from tests.test_ocr_layout import layout_pdf


@unittest.skipUnless(importlib.util.find_spec("pymupdf") and importlib.util.find_spec("PIL"),
                     "Установите requirements-ocr.txt")
class ImageOcrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="картинки OCR ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = OcrSettings(self.root / "models")

    def test_mixed_pdf_jpeg_png_corrupt_and_unsupported_keep_order_and_originals(self):
        from PIL import Image
        first = self.root / "Первый скан.JPG"
        second = self.root / "Второй скан.png"
        for path in (first, second):
            with Image.new("RGB", (400,300), "white") as image:
                image.save(path)
        bad = self.root / "повреждённый.png"
        bad.write_bytes(b"NOT AN IMAGE")
        unsupported = self.root / "file.txt"
        unsupported.write_text("Not a supported input")
        pdf = make_pdf(self.root / "Третий.pdf")
        paths=[first,bad,second,pdf,unsupported]
        originals = {p:p.read_bytes() for p in paths}
        backend=Mock()
        backend.recognize.side_effect=["FIRST IMAGE TEXT", "SECOND IMAGE TEXT"]
        output=self.root / "result"
        result=process_batch(paths,output,self.settings,backend=backend)
        self.assertEqual(result["status"],"partial",result)
        self.assertEqual(result["pages_total"],3)
        self.assertEqual(result["pages_done"],3)
        self.assertEqual(result["errors"],2)
        self.assertEqual(backend.recognize.call_count,2)
        self.assertEqual([f["page_count"] for f in result["files"]],[1,0,1,1,0])
        text=(output/result["text_path"]).read_text(encoding="utf-8")
        self.assertLess(text.index("FIRST IMAGE TEXT"),text.index("SECOND IMAGE TEXT"))
        self.assertLess(text.index("SECOND IMAGE TEXT"),text.index("OCTOPUS OCR"))
        self.assertTrue((output/result["docx_path"]).is_file())
        for path in paths:
            self.assertEqual(path.read_bytes(),originals[path])

    def test_exif_rotation_transparency_and_input_resolution(self):
        from PIL import Image, ImageDraw
        transparent=self.root/"прозрачный.png"
        with Image.new("RGBA",(120,80),(0,0,0,0)) as image:
            ImageDraw.Draw(image).rectangle((20,20,40,40),fill=(0,0,0,255))
            image.save(transparent)
        rotated=self.root/"поворот.jpeg"
        with Image.new("RGB",(120,80),"white") as image:
            ImageDraw.Draw(image).rectangle((0,0,20,20),fill="black")
            exif=Image.Exif();exif[274]=6
            image.save(rotated,exif=exif,quality=100)
        captures=[]
        backend=Mock()
        backend.recognize.side_effect=lambda image: captures.append(image.copy()) or "TEXT"
        result=process_batch([transparent,rotated],self.root/"result",self.settings,backend=backend)
        self.assertEqual(result["status"],"completed",result)
        self.assertTrue((captures[0][0,0] == 255).all())
        self.assertLess(int(captures[0][30,30].max()),20)
        self.assertGreater(captures[1].shape[0], captures[1].shape[1])
        self.assertLess(int(captures[1][5,70].max()),40)
        # DPI настройки PDF не увеличивает картинку при 300 DPI.
        backend.reset_mock()
        captures.clear()
        process_batch([transparent],self.root/"dpi300",OcrSettings(self.root,dpi=300),backend=backend)
        self.assertLessEqual(captures[0].shape[1],121)
        self.assertLessEqual(captures[0].shape[0],81)

    def test_image_long_side_is_capped_and_cancel_keeps_first_image(self):
        from PIL import Image
        large=self.root/"wide.png"
        with Image.new("RGB",(4200,100),"white") as image:
            image.save(large)
        captures=[]
        backend=Mock()
        backend.recognize.side_effect=lambda image: captures.append(image.shape) or "READY"
        flag=[False]
        def event(item):
            if item["event"]=="page_finished":flag[0]=True
        output=self.root/"cancel"
        result=process_batch([large,large],output,self.settings,event,lambda:flag[0],backend=backend)
        self.assertEqual(result["status"],"cancelled")
        self.assertEqual(result["pages_done"],1)
        self.assertLessEqual(max(captures[0][:2]),3500)
        self.assertTrue((output/"result-partial.docx").exists())

    def test_images_reconstruct_editable_tables_including_merged_cells(self):
        import pymupdf
        from docx import Document
        from PIL import Image
        source=self.root/"source.pdf"
        layout_pdf(source)
        tokens=[]
        with pymupdf.open(source) as pdf:
            page=pdf[0]
            scale=IMAGE_DPI/72
            pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale))
            png=self.root/"table.png"
            pix.save(png)
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines",[]):
                    x0,y0,x1,y1=line["bbox"]
                    tokens.append(([[x0*scale,y0*scale],[x1*scale,y0*scale],[x1*scale,y1*scale],[x0*scale,y1*scale]],
                                   "".join(s["text"] for s in line["spans"]),.99))
        jpg=self.root/"table.jpg"
        with Image.open(png) as image:
            image.save(jpg,quality=95)
        for path in (png,jpg):
            with self.subTest(format=path.suffix):
                backend=Mock()
                backend.recognize_layout.return_value=tokens
                output=self.root/path.suffix[1:]
                result=process_batch([path],output,OcrSettings(self.root,preserve_layout=True),backend=backend)
                self.assertEqual(result["status"],"completed",result)
                doc=Document(output/result["docx_path"])
                self.assertEqual(len(doc.tables),1)
                self.assertEqual(doc.tables[0].cell(1,0).text,"A1")
                self.assertTrue(doc.tables[0].cell(0,0)._tc.xpath("./w:tcPr/w:gridSpan"))
                backend.recognize.assert_not_called()

    def test_animated_png_is_rejected_instead_of_silently_losing_frames(self):
        from PIL import Image
        source=self.root/"animated.png"
        with Image.new("RGB",(40,40),"white") as first, Image.new("RGB",(40,40),"black") as second:
            first.save(source,save_all=True,append_images=[second],duration=100,loop=0)
        with self.assertRaisesRegex(ValueError,"Анимированный"):
            inspect_input(source)

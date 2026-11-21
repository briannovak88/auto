import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ocr_engine import OcrSettings, process_batch


def layout_pdf(path):
    import pymupdf
    pdf = pymupdf.open()
    page = pdf.new_page(width=500,height=650)
    page.insert_text((40,40), "SOURCE DOCUMENT TITLE", fontsize=18)
    for y in (80,120,160,200):
        page.draw_line((40,y),(440,y),width=1)
    for x in (40,440):
        page.draw_line((x,80),(x,200),width=1)
    page.draw_line((240,120),(240,200),width=1)
    for x,y,text in ((55,105,"MERGED HEADER"),(55,145,"A1"),(255,145,"B1"),(55,185,"A2"),(255,185,"B2")):
        page.insert_text((x,y),text,fontsize=11)
    for index in range(4):
        page.insert_text((40,260+index*20),f"Left column line {index+1}",fontsize=11)
        page.insert_text((280,260+index*20),f"Right column line {index+1}",fontsize=11)
    pdf.save(path)
    pdf.close()


@unittest.skipUnless(importlib.util.find_spec("pymupdf"), "Установите requirements-ocr.txt")
class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="layout-тест ")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'таблица и колонки.pdf'
        layout_pdf(self.source)
        self.settings=OcrSettings(self.root/'models',preserve_layout=True)

    def assert_editable_structure(self,output,result):
        from docx import Document
        from docx.oxml.ns import qn
        doc=Document(output/result['docx_path'])
        self.assertEqual(len(doc.tables),1)
        table=doc.tables[0]
        self.assertEqual(len(table.rows),3)
        self.assertEqual(len(table.columns),2)
        self.assertIn('MERGED HEADER',table.cell(0,0).text)
        self.assertEqual(table.cell(1,0).text,'A1')
        self.assertEqual(table.cell(2,1).text,'B2')
        self.assertTrue(table.cell(0,0)._tc.xpath('./w:tcPr/w:gridSpan'))
        counts=[s._sectPr.find(qn('w:cols')).get(qn('w:num')) for s in doc.sections]
        self.assertIn('2',counts)
        text='\n'.join(p.text for p in doc.paragraphs)
        for item in ('Left column line 1','Right column line 4'):
            self.assertIn(item,text)
        self.assertLess(text.index('Left column line 4'),text.index('Right column line 1'))
        self.assertEqual(doc.sections[0].page_width.pt,500)
        self.assertEqual(doc.sections[0].page_height.pt,650)
        self.assertIn('Страница 1',doc.sections[0].header.paragraphs[0].text)
        self.assertFalse(doc.inline_shapes)

    def test_native_pdf_real_merged_table_and_columns(self):
        output=self.root/'native'
        backend=Mock()
        result=process_batch([self.source],output,self.settings,backend=backend)
        self.assertEqual(result['status'],'completed',result)
        backend.recognize.assert_not_called()
        self.assert_editable_structure(output,result)
        record=result['files'][0]['pages'][0]
        self.assertTrue((output/record['layout_file']).exists())

    def test_scan_grid_and_columns_from_ocr_coordinates(self):
        import pymupdf
        with pymupdf.open(self.source) as source:
            page=source[0]
            raster=page.get_pixmap(matrix=pymupdf.Matrix(2,2))
            # Синтетический OCR-ответ с координатами; модель здесь не имитирует структуру.
            tokens=[]
            scale=self.settings.dpi/72
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    x0,y0,x1,y1=line['bbox']
                    tokens.append(([[x0*scale,y0*scale],[x1*scale,y0*scale],[x1*scale,y1*scale],[x0*scale,y1*scale]],
                                   ''.join(s['text'] for s in line['spans']),.99))
            scanned=pymupdf.open()
            target=scanned.new_page(width=500,height=650)
            target.insert_image(target.rect,stream=raster.tobytes('png'))
            scan=self.root/'scan.pdf';scanned.save(scan);scanned.close()
        backend=Mock()
        backend.recognize_layout.return_value=tokens
        output=self.root/'scan-output'
        result=process_batch([scan],output,self.settings,backend=backend)
        self.assertEqual(result['status'],'completed',result)
        backend.recognize_layout.assert_called_once()
        backend.recognize.assert_not_called()
        self.assert_editable_structure(output,result)

    def test_layout_failure_keeps_text_and_marks_warning(self):
        output=self.root/'fallback'
        with patch('ocr_layout.native_layout',side_effect=RuntimeError('synthetic failure')):
            result=process_batch([self.source],output,self.settings)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['layout_warnings'],1)
        self.assertEqual(result['errors'],0)
        self.assertIn('Left column line 1',(output/result['text_path']).read_text(encoding='utf-8'))
        self.assertTrue((output/result['docx_path']).is_file())

    def test_cancelled_layout_result_stays_editable_and_partial(self):
        flag=[False]
        def event(item):
            if item['event']=='page_finished':flag[0]=True
        output=self.root/'cancel'
        result=process_batch([self.source],output,self.settings,event,lambda:flag[0])
        self.assertEqual(result['status'],'cancelled')
        self.assert_editable_structure(output,result)
        self.assertFalse((output/'result.docx').exists())


if __name__=='__main__':
    unittest.main()

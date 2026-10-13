"""Testes isolados do PDF. Não acessa Neon, não altera pedidos reais."""
import io
import unittest
from unittest import mock
from PIL import Image
from pypdf import PdfReader
import app

def jpg_bytes(mode):
    pic=Image.new(mode,(100,80),(255,30,60) if mode=='RGB' else 133)
    output=io.BytesIO()
    pic.save(output,format='JPEG')
    return output.getvalue()

def order(idx):
    return {
        'id': ('a' if idx==1 else 'b')*32,
        'code': 'GC-2026-%04d'%idx,
        'entry_date':'2026-10-13',
        'due_date':'2026-10-14',
        'client':'Cliente de teste',
        'phone':'86999999999',
        'service':'Adesivo personalizado',
        'quantity':2,
        'priority':'Normal',
        'status':'Em produção',
        'notes':'Pedido para impressão com foto.',
        'total':150.0,'received':50.0,'balance':100.0,'payment':'Parcial'
    }

def sample_images(color):
    bw=color=='bw'
    return [{'width':100,'height':80,'name':'foto exemplo.jpg',
             'gray':bw,'data':jpg_bytes('L' if bw else 'RGB')}]

class PdfOsTests(unittest.TestCase):
    def test_single_a5_color_and_photo(self):
        with mock.patch.object(app,'load_os_photos',side_effect=lambda oid,color:sample_images(color)):
            raw=app.make_pdf(order(1),'a5',color='color')
        reader=PdfReader(io.BytesIO(raw))
        self.assertEqual(len(reader.pages),1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width),app.PW,places=2)
        pictures=reader.pages[0]['/Resources']['/XObject']
        ref=pictures['/Foto0'].get_object()
        self.assertEqual(str(ref['/ColorSpace']),'/DeviceRGB')
        self.assertEqual(str(ref['/Filter']),'/DCTDecode')
    def test_single_a4_bw_and_photo(self):
        with mock.patch.object(app,'load_os_photos',side_effect=lambda oid,color:sample_images(color)):
            raw=app.make_pdf(order(1),'a4',color='bw')
        reader=PdfReader(io.BytesIO(raw))
        self.assertEqual(len(reader.pages),1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height),app.A4_H,places=2)
        ref=reader.pages[0]['/Resources']['/XObject']['/Foto0'].get_object()
        self.assertEqual(str(ref['/ColorSpace']),'/DeviceGray')
        self.assertTrue(b'/Foto0 Do' in reader.pages[0].get_contents().get_data())
    def test_double_pdf_has_separate_photos(self):
        with mock.patch.object(app,'load_os_photos',side_effect=lambda oid,color:sample_images(color)):
            raw=app.make_pdf_double(order(1),order(2),color='color')
        reader=PdfReader(io.BytesIO(raw))
        self.assertEqual(len(reader.pages),1)
        names=reader.pages[0]['/Resources']['/XObject']
        self.assertIn('/Foto0',names)
        self.assertIn('/Foto1',names)
        self.assertIn(b'/Foto1 Do',reader.pages[0].get_contents().get_data())
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width),app.A4_LAND_W,places=2)
    def test_photo_and_long_notes_never_open_another_page(self):
        data=order(1)
        data['notes']='Observação detalhada da produção e acabamento. '*200
        data['service']='Banner adesivo com acabamento especial personalizado. '*50
        with mock.patch.object(app,'load_os_photos',side_effect=lambda oid,color:sample_images(color)*3):
            raw=app.make_pdf(data,'a5',color='color')
        pdf=PdfReader(io.BytesIO(raw))
        self.assertEqual(len(pdf.pages),1)
        # A primeira foto fica na mesma página e as demais permanecem como anexos.
        xobjs=pdf.pages[0]['/Resources']['/XObject']
        self.assertIn('/Foto0',xobjs)
        self.assertNotIn('/Foto1',xobjs)
        self.assertIn(b'/Foto0 Do',pdf.pages[0].get_contents().get_data())
    def test_double_with_photo_only_on_second_os(self):
        with mock.patch.object(app,'load_os_photos',side_effect=[[],sample_images('color')]):
            raw=app.make_pdf_double(order(1),order(2),color='color')
        pdf=PdfReader(io.BytesIO(raw))
        self.assertEqual(len(pdf.pages),1)
        self.assertIn(b'/Foto0 Do',pdf.pages[0].get_contents().get_data())
        self.assertNotIn('/Foto1',pdf.pages[0]['/Resources']['/XObject'])
    def test_without_photos_still_one_page(self):
        with mock.patch.object(app,'load_os_photos',return_value=[]):
            raw=app.make_pdf(order(1),'a5',color='color')
        self.assertEqual(len(PdfReader(io.BytesIO(raw)).pages),1)
    def test_invalid_color_fails(self):
        with self.assertRaises(app.AppError):
            app.make_pdf(order(1),'a5',color='invalid')
        with self.assertRaises(app.AppError):
            app.make_pdf_double(order(1),order(2),color='invalid')
    def test_palette_gray(self):
        pages,_=app.make_os_pages(order(1),color='bw')
        import re
        commands=pages[0]
        found=0
        for command in commands:
            for match in re.finditer(r'([0-9.]+) ([0-9.]+) ([0-9.]+) rg',command):
                a,b,c=map(float,match.groups())
                self.assertAlmostEqual(a,b,places=3)
                self.assertAlmostEqual(b,c,places=3)
                found+=1
        self.assertGreater(found,5)

    def test_original_jpeg_from_database_is_embedded_and_resized(self):
        from contextlib import contextmanager
        raw=jpg_bytes('RGB')
        class Db:
            def execute(self,sql,args):
                class Cursor:
                    def __init__(self,content):self.content=content
                    def fetchall(self):return self.content
                    def fetchone(self):return self.content
                if 'SELECT id,filename,size FROM attachments' in sql:
                    return Cursor([{'id':'f'*32,'filename':'foto_cliente.jpg','size':len(raw)}])
                if 'SELECT data FROM attachments' in sql:
                    return Cursor({'data':raw})
                raise AssertionError('Consulta inesperada')
        @contextmanager
        def fake_connect():
            yield Db()
        with mock.patch.object(app,'connect',fake_connect):
            photos=app.load_os_photos('a'*32,'bw')
        self.assertEqual(len(photos),1)
        self.assertEqual(photos[0]['name'],'foto_cliente.jpg')
        self.assertTrue(photos[0]['gray'])
        with Image.open(io.BytesIO(photos[0]['data'])) as jpeg:
            self.assertEqual(jpeg.mode,'L')
            self.assertEqual(jpeg.size,(100,80))

if __name__=='__main__':
    unittest.main()

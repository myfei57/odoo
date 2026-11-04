# Part of Odoo. See LICENSE file for full copyright and licensing details.
import csv
import io
import json
import zipfile

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestCsvExport(HttpCase):

    def _create_partners(self):
        """ Create 4 partners with respectively 2, 1, 0 and 3 children. """
        Partner = self.env['res.partner']
        partners = []
        children_counts = [2, 1, 0, 3]
        for index, count in enumerate(children_counts, start=1):
            children = Partner.create([
                {'name': f'__csv_export_{index}_child_{child_index}'}
                for child_index in range(count)
            ])
            # A formula-like name checks that the formula protection still applies
            name = '=1+2' if index == 1 else f'__csv_export_parent_{index}'
            partners.append(Partner.create({
                'name': name,
                'child_ids': [(6, 0, children.ids)],
            }))
        return partners

    def _export(self, ids=None, domain=None, chunk_size=None):
        payload = {
            'model': 'res.partner',
            'ids': ids or False,
            'domain': domain if domain is not None else [],
            'import_compat': False,
            'fields': [
                {'name': 'name', 'label': 'Name'},
                {'name': 'child_ids/name', 'label': 'Child Name'},
            ],
            'context': {},
        }
        if chunk_size is not None:
            payload['chunk_size'] = chunk_size
        self.authenticate('admin', 'admin')
        return self.url_open('/web/export/csv', data={
            'data': json.dumps(payload),
            'csrf_token': self.csrf_token(),
        })

    @staticmethod
    def _read_csv(content):
        return list(csv.reader(io.StringIO(content.decode('utf-8'))))

    def test_csv_export_without_chunk_size_is_a_single_csv(self):
        partners = self._create_partners()
        response = self._export(ids=partners.ids)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers['Content-Type'].startswith('text/csv'))
        self.assertIn('.csv', response.headers['Content-Disposition'])
        self.assertNotIn('.zip', response.headers['Content-Disposition'])

        rows = self._read_csv(response.content)
        self.assertEqual(rows[0], ['Name', 'Child Name'])
        # 4 main records with 2, 1, 0 and 3 children expanding to 2 + 1 + 1 + 3 = 7 data rows
        self.assertEqual(len(rows), 8)

    def test_csv_export_chunked_in_zip(self):
        partners = self._create_partners()
        response = self._export(ids=partners.ids, chunk_size=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        self.assertIn('.zip', response.headers['Content-Disposition'])

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            volume_names = [name for name in archive.namelist() if name != 'manifest.csv']

            # Manifest first, then continuously numbered volumes sharing a base name
            self.assertEqual(archive.namelist(), ['manifest.csv', *volume_names])
            self.assertEqual(len(volume_names), 2)
            self.assertTrue(volume_names[0].endswith('_001.csv'))
            self.assertTrue(volume_names[1].endswith('_002.csv'))
            self.assertEqual(
                volume_names[0][:-len('_001.csv')],
                volume_names[1][:-len('_002.csv')],
            )

            volume_rows = [self._read_csv(archive.read(name)) for name in volume_names]
            for rows in volume_rows:
                self.assertEqual(rows[0], ['Name', 'Child Name'])

            # 2 main records per volume, expanding to 3 and 4 data rows
            self.assertEqual(len(volume_rows[0]), 4)
            self.assertEqual(len(volume_rows[1]), 5)

            # The expanded lines of a record stay in the same volume: the first
            # child is merged on the main line and the other children are appended
            # with a blank first column; no main record is split across volumes.
            first_column_1 = [row[0] for row in volume_rows[0][1:]]
            self.assertEqual(first_column_1, ["'=1+2", '', '__csv_export_parent_2'])
            first_column_2 = [row[0] for row in volume_rows[1][1:]]
            self.assertEqual(first_column_2, ['__csv_export_parent_3', '__csv_export_parent_4', '', ''])

            manifest = self._read_csv(archive.read('manifest.csv'))
            self.assertEqual(
                manifest[0],
                ['filename', 'main_record_count', 'data_row_count'],
            )
            self.assertEqual(manifest[1], [volume_names[0], '2', '3'])
            self.assertEqual(manifest[2], [volume_names[1], '2', '4'])

        # The concatenation of the volumes equals the regular single-file export,
        # in the exact same order (only the per-volume headers differ).
        single_rows = self._read_csv(self._export(ids=partners.ids).content)
        rebuilt_rows = [['Name', 'Child Name']]
        rebuilt_rows += [row for rows in volume_rows for row in rows[1:]]
        self.assertEqual(rebuilt_rows, single_rows)

    def test_csv_export_chunk_size_one_keeps_records_unsplit(self):
        partners = self._create_partners()
        response = self._export(ids=partners.ids, chunk_size=1)

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            volume_names = [name for name in archive.namelist() if name != 'manifest.csv']
            self.assertEqual(archive.namelist(), ['manifest.csv', *volume_names])
            self.assertEqual(len(volume_names), 4)
            self.assertTrue(volume_names[0].endswith('_001.csv'))
            self.assertTrue(volume_names[3].endswith('_004.csv'))

            # Each volume holds one main record (with all its expanded lines)
            data_row_counts = [len(self._read_csv(archive.read(name))) - 1 for name in volume_names]
            self.assertEqual(data_row_counts, [2, 1, 1, 3])

            for name in volume_names:
                rows = self._read_csv(archive.read(name))
                self.assertEqual(rows[0], ['Name', 'Child Name'])

            manifest = self._read_csv(archive.read('manifest.csv'))
            self.assertEqual(
                manifest[1:],
                [[name, '1', str(count)] for name, count in zip(volume_names, data_row_counts)],
            )

    def test_csv_export_chunked_empty_result(self):
        response = self._export(domain=[('id', '=', -999999)], chunk_size=10)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            volume_names = [name for name in archive.namelist() if name != 'manifest.csv']
            self.assertEqual(archive.namelist(), ['manifest.csv', *volume_names])
            self.assertEqual(len(volume_names), 1)
            self.assertTrue(volume_names[0].endswith('_001.csv'))

            rows = self._read_csv(archive.read(volume_names[0]))
            self.assertEqual(rows, [['Name', 'Child Name']])

            manifest = self._read_csv(archive.read('manifest.csv'))
            self.assertEqual(manifest, [
                ['filename', 'main_record_count', 'data_row_count'],
                [volume_names[0], '0', '0'],
            ])

    def test_csv_export_chunked_invalid_size_rejected(self):
        partners = self._create_partners()
        for invalid_chunk_size in (0, -1, 2.5, '2', True):
            response = self._export(ids=partners.ids, chunk_size=invalid_chunk_size)
            self.assertEqual(response.status_code, 500, invalid_chunk_size)
            payload = json.loads(response.content)
            self.assertIn('positive integer', json.dumps(payload))

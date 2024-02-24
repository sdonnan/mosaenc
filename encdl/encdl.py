from pathlib import Path
from tempfile import TemporaryDirectory
from multiprocessing import cpu_count
from zipfile import ZipFile, LargeZipFile, BadZipFile
from typing import List
from enum import Enum, auto
import concurrent.futures
import logging
import subprocess

'''
MB tile builder

To Do:
* Consider generating a build script (ninja) to avoid redoing work
'''

LOG = logging.getLogger(__name__)
ENC_ALL_URL = 'https://charts.noaa.gov/ENCs/All_ENCs.zip'
S57_DAL_OPTIONS = {
    'RETURN_PRIMITIVES': 'ON',
    'RETURN_LINKAGES': 'ON',
    'LNAM_REFS': 'ON',
    'SPLIT_MULTIPOINT': 'ON',
    'ADD_SOUNDG_DEPTH': 'ON',
}

BAND2ZOOM = {
    1: (0,1),
    2: (2,6),
    3: (7,11),
    4: (12,13),
    5: (14,15),
    6: (16,18),
}

class DataType:
    Point = auto()
    Line = auto()
    Polygon = auto()

    def as_gdal(dt):
        if dt == DataType.Point: return 'POINT25D'
        if dt == DataType.Line: return 'MULTILINESTRING'
        if dt == DataType.Polygon: return 'MULTIPOLYGON'
        raise NotImplementedError(f'Unknown type {dt}')

CFG = {
    'polygons': {
        'LNDARE': {
            'color': (1.0, 0.906, 0.671, 1.0)
        }
    },
    'lines': {
        'COALNE': {
            'color': (0.459, 0.329, 0.0, 1.0)
        },
        'DEPCNT': {
            'color': (0.047, 0.0, 0.561, 1.0)
        },
    },
    'background': (0.843, 0.827, 1.0, 1.0),
    'soundings': True,
}

def get_enc(work_dir: Path):
    '''Download the ENC files and decompress them in the working directory'''

    with TemporaryDirectory() as dlpath:

        # download

        LOG.debug('Starting ENC zip file download')
        zipfname = Path(dlpath, 'enc.zip')

        proc = subprocess.run([
            'curl',
            '--no-progress-meter',
            '-fo',
            zipfname,
            ENC_ALL_URL
        ], capture_output=True)

        if proc.returncode != 0:
            emsg = proc.stderr.decode()
            LOG.error(f'Failed to download. {emsg}')
            raise Exception('Download failed')

        # unpack

        LOG.debug('Starting unzip')
        try:
            zipfile = ZipFile(zipfname)
            zipfile.extractall(work_dir)
        except (BadZipFile, LargeZipFile) as exception:
            LOG.error(f'Failed to extract zip: {exception}')
            raise (exception)

def make_dbs(work_dir: Path, jobs=1):
    work_dir = Path(work_dir)
    out_dir = Path(work_dir, 'dbs')
    enc_dir = Path(work_dir, 'ENC_ROOT')
    if not out_dir.is_dir():
        out_dir.mkdir()
    output_dbs = dict()
    # find dirs to coallesce
    for entry in enc_dir.iterdir():
        if entry.is_dir():
            key = (entry.stem[2], entry.stem[3:5])
            output_dbs.setdefault(key, list()).append(entry)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as exec:
        process_files = dict()
        for out_stem, directories in output_dbs.items():
            directories.sort()
            outf = Path(out_dir, out_stem[1] + out_stem[0] + '.db')
            future = exec.submit(process_enc_dirs, directories, outf)
            process_files[future] = outf
    for future in concurrent.futures.as_completed(process_files):
        try:
            future.result()
            process_files[future]
        except Exception as e:
            LOG.error(e)
            pass

    return process_files

def process_enc_dirs(directories: List[Path], outf: Path):
    for source in directories:
        inf = Path(source, source.stem + '.000')
        export_enc(inf, outf, CFG.get('polygons', []).keys(), DataType.Polygon)
        export_enc(inf, outf, CFG.get('lines', []).keys(), DataType.Line)
        if CFG.get('soundings', True):
            export_enc(inf, outf, ['SOUNDG'], DataType.Point)

def export_enc(infile: Path, outfile: Path, layers: List[str], content_type: DataType):

    LOG.debug(f'{infile} -> {outfile}')
    band = int(infile.stem[2])
    proc = subprocess.run([
        'ogr2ogr',
        '-append' if outfile.exists() else '-overwrite',
        '-mo',
        f'band={band}',
        '-nlt',
        f'{DataType.as_gdal(content_type)}',
        '-skipfailures',
        '-f',
        'sqlite',
        outfile,
        infile,
        *layers
    ], 
    env = S57_DAL_OPTIONS,
    capture_output = True)

    if proc.returncode != 0:
        emsg = proc.stderr.decode()
        msg = f'Failed to generated DB. {emsg}'
        LOG.error(msg)

def process_dbs(work_dir: Path, jobs=1):

    work_dir = Path(work_dir)
    out_dir = Path(work_dir, 'mbtiles')
    dbs_dir = Path(work_dir, 'dbs')
    outfiles = set()
    # build geojson for tippecanoe input
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as exec:
        process_files = dict()
        for db in dbs_dir.iterdir():
            if db.is_file() and db.suffix == '.db':
                # TODO don't hardcode this
                outfiles.add(db.stem[:4])
                for layer in ('LNDARE', 'COALNE', 'DEPCNT'):
                    outf = Path(out_dir, f'{db.stem}_{layer}.geojson')
                    if outf.exists(): outf.unlink()
                    future = exec.submit(process_db, db, outf, layer)
                    process_files[future] = outf
    for future in concurrent.futures.as_completed(process_files):
        try:
            future.result()
        except Exception as e:
            LOG.error(e)
            pass
    # build mbtiles
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as exec:
        process_files = dict()
        for out_stem in outfiles:
            infiles = list()
            for file in out_dir.iterdir():
                if file.is_file() and file.suffix == '.geojson' and file.stem.startswith(out_stem):
                    infiles.append(file)
            outf = Path(out_dir, out_stem + '.mbtiles')
            future = exec.submit(mkmbtiles, infiles, outf, None)
            process_files[future] = outf
    for future in concurrent.futures.as_completed(process_files):
        try:
            future.result()
        except Exception as e:
            LOG.error(e)
            pass

    return process_files

def process_db(infile: Path, outfile: Path, layer: str):

    LOG.debug(f'{infile} -> {outfile}')
    proc = subprocess.run([
        'ogr2ogr',
        '-f',
        'geojson',
        outfile,
        infile,
        layer
    ], 
    capture_output = True)

    if proc.returncode != 0:
        emsg = proc.stderr.decode()
        msg = f'Failed to generated DB. {emsg}'
        LOG.error(msg)

def mkmbtiles(infiles: List[Path], outfile: Path, dtype: DataType):

    LOG.debug(f'{[ f.stem for f in infiles]} -> {outfile}')
    band = int(outfile.stem[2])
    zoom = BAND2ZOOM[band]
    proc = subprocess.run([
        'tippecanoe',
        '-zg',
        '--coalesce-densest-as-needed',
        '--extend-zooms-if-still-dropping',
        '-fo',
        outfile,
        *infiles,
    ], 
    capture_output = True)

    if proc.returncode != 0:
        emsg = proc.stderr.decode()
        msg = f'Failed to generated mbtiles. {emsg}'
        LOG.error(msg)


if __name__ == '__main__':

    from argparse import ArgumentParser
    logging.basicConfig()
    LOG.setLevel(logging.WARN)

    parser = ArgumentParser(
        'Process ENCs into mbtiles file using GDAL and Tippecanoe')
    parser.add_argument('-d', '--work-dir', type=Path, default=Path.cwd(),
                        help='Working directory for script if not the current directory')
    parser.add_argument('-v', '--verbose',
                        action='store_true', help='Debug output')
    parser.add_argument('-g', '--get', action='store_true',
                        help='Download ENCs from NOAA. If not should be in ${WORK_DIR}/ENC_ROOT')
    parser.add_argument('-j', '--jobs', type=int, default=cpu_count(),
                        help='Download ENCs from NOAA. If not should be in ${WORK_DIR}/ENC_ROOT')
    parser.add_argument('--db', action='store_true',
                        help='Generate intermediate db representation')

    args = parser.parse_args()

    if args.verbose:
        LOG.setLevel(logging.DEBUG)

    if args.get:
        LOG.debug('Downloading ENC files')
        get_enc(args.work_dir)

    encdir = Path(args.work_dir, 'ENC_ROOT')
    if not encdir.is_dir:
        log.error('ENC_ROOT does not exist. Should you use --get to download?')

    if args.db:
        make_dbs(args.work_dir, args.jobs)

    process_dbs(args.work_dir, jobs=args.jobs)
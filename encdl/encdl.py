from enum import Enum, auto
from multiprocessing import cpu_count
from osgeo import ogr
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Dict
from zipfile import ZipFile, LargeZipFile, BadZipFile
import concurrent.futures
import json
import logging
import os
import subprocess

'''
MB tile builder

To Do:
* Consider generating a build script (ninja) to avoid redoing work
'''

# new ogr needs this set
try:
    ogr.UseExceptions()
except:
    pass

LOG = logging.getLogger(__name__)
ENC_ALL_URL = 'https://charts.noaa.gov/ENCs/All_ENCs.zip'
OGR_S57_OPTIONS = "RETURN_PRIMITIVES=ON,RETURN_LINKAGES=ON,LNAM_REFS=ON,SPLIT_MULTIPOINT=ON,ADD_SOUNDG_DEPTH=ON"

BAND2ZOOM = {
    1: (0, 6),
    2: (6, 10),
    3: (10, 12),
    4: (12, 14),
    5: (14, 16),
    6: (16, 18),
}

MAXZ = 18


class DataType:
    Point = auto()
    Line = auto()
    Polygon = auto()

    def as_gdal(dt):
        if dt == DataType.Point:
            return 'POINT25D'
        if dt == DataType.Line:
            return 'MULTILINESTRING'
        if dt == DataType.Polygon:
            return 'MULTIPOLYGON'
        raise NotImplementedError(f'Unknown type {dt}')


class Enc:

    def __init__(self, fname: Path):
        self.fname: Path = fname
        self.band: int(fname.stem[2])
        self.bb = None
        drv = ogr.GetDriverByName('S57')
        try:
            ds = drv.Open(str(self.fname))
            self.bb = ds.GetLayer(1).GetExtent()
        except Exception as e:
            LOG.error(f'Error reading S57 file: {e}')

    def within(self, env):
        if self.bb is None:
            return False
        return \
            env[0] < self.bb[0] and \
            env[1] > self.bb[1] and \
            env[2] < self.bb[2] and \
            env[3] > self.bb[3]

    def intersects(self, env):
        if self.bb is None:
            return False
        ax1, ax2, ay1, ay2 = env
        bx1, bx2, by1, by2 = self.bb
        return not (
            ax1 > bx2 or
            ax2 < bx1 or
            ay1 > by2 or
            ay2 < by1
        )


CFG = {
    'layers': {
        'LNDARE': {
            'type': DataType.Polygon
        },
        #        'SEAARE': {
        #            'type': DataType.Polygon
        #        },
        #        'COALNE': {
        #            'type': DataType.Line
        #        },
        'DEPCNT': {
            'type': DataType.Line
        },
        'SOUNDG': {
            'type': DataType.Point
        },
    },
    'background': (0.843, 0.827, 1.0, 1.0)
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


def make_geojsons(work_dir: Path, bb: List[float], jobs=1):
    work_dir = Path(work_dir)
    out_dir = Path(work_dir, 'geojsons')
    enc_dir = Path(work_dir, 'ENC_ROOT')
    if not out_dir.is_dir():
        out_dir.mkdir()
    output_geojsons = dict()
    # find dirs to coallesce
    for entry in enc_dir.iterdir():
        if entry.is_dir():
            key = (entry.stem[2], 'US')  # entry.stem[3:5])
            output_geojsons.setdefault(key, list()).append(entry)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as exec:
        process_files = dict()
        for out_stem, directories in output_geojsons.items():
            infiles = list(filter(
                lambda x: Enc(x).intersects(bb),
                [Path(source, source.stem + '.000') for source in directories],
            ))
            if not infiles:
                continue
            for infile in infiles:
                for layer, lconf in CFG['layers'].items():
                    outf = Path(out_dir, f'{infile.stem}_{layer}.geojson')
                    future = exec.submit(
                        process_encs, infile, outf, lconf['type'], layer)
                    process_files[future] = outf
    for future in concurrent.futures.as_completed(process_files):
        try:
            future.result()
            process_files[future]
        except Exception as e:
            LOG.error(e)
            pass

    return process_files


def process_encs(infile: Path, outfile: Path, dtype: DataType, layer: str):

    LOG.debug(f'{infile.name} -> {outfile.name}')
    env = os.environ
    env['OGR_S57_OPTIONS'] = OGR_S57_OPTIONS

    proc = subprocess.run([
        'ogr2ogr',
        '-nlt',
        DataType.as_gdal(dtype),
        '-skipfailures',
        '-f',
        'geojson',
        outfile,
        infile,
        layer
    ],
        env=env,
        capture_output=True)

    if proc.returncode != 0:
        emsg = proc.stdout.decode()
        msg = f'Failed to generate GeoJSON for {infile.name}. {emsg}'
        LOG.error(msg)


def process_geojsons(work_dir: Path, jobs=1):

    work_dir = Path(work_dir)
    out_dir = Path(work_dir, 'mbtiles')
    geojsons_dir = Path(work_dir, 'geojsons')
    if not out_dir.is_dir():
        out_dir.mkdir()
    bandfiles = dict()
    # build mbtiles
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as exec:
        process_files = dict()
        for layer, lconf in CFG['layers'].items():
            for band in range(9, 0, -1):
                infiles = list()
                outf = Path(out_dir, f'BAND{band}_{layer}.mbtiles')
                for file in geojsons_dir.glob(f'US{band}*{layer}.geojson'):
                    infiles.append(file)
                if not infiles:
                    continue
                bandfiles.setdefault(band, list()).append(outf)
                future = exec.submit(
                    mkmbtiles, infiles, outf, lconf['type'], layer, BAND2ZOOM[band][1])
                process_files[future] = outf
    for future in concurrent.futures.as_completed(process_files):
        try:
            future.result()
        except Exception as e:
            LOG.error(e)
            pass

    # merge tiles
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as exec:
        process_files = dict()
        for band, infiles in bandfiles.items():
            outf = Path(out_dir, f'BAND{band}.mbtiles')
            future = exec.submit(mergetiles, infiles, outf)
            process_files[future] = outf

    for future in concurrent.futures.as_completed(process_files):
        try:
            future.result()
        except Exception as e:
            LOG.error(e)
            pass

    return process_files


def mkmbtiles(infiles: List[Path], outfile: Path, dtype: DataType, layer: str, z: int = 18):

    LOG.debug(f'{[ f.name for f in infiles]} -> {outfile.name}')
    if dtype == DataType.Point:
        args = ['-r1', '--cluster-distance=10',
                '--accumulate-attribute=DEPTH:mean', '-yDEPTH']
    else:
        args = ['--coalesce-densest-as-needed',
                '--extend-zooms-if-still-dropping', '-yVALDCO', '-yOBJNAM']

    proc = subprocess.run([
        'tippecanoe',
        *args,
        '-l',
        layer,
        f'-z{z}',
        '-fo',
        outfile,
        *infiles,
    ],
        capture_output=True)

    if proc.returncode != 0:
        emsg = proc.stderr.decode()
        msg = f'Failed to generate mbtiles. {emsg}'
        LOG.error(msg)


def mergetiles(infiles: List[Path], outfile: Path):

    LOG.debug(f'{[ f.stem for f in infiles]} -> {outfile}')

    proc = subprocess.run([
        'tile-join',
        '-fo',
        outfile,
        *infiles,
    ],
        capture_output=True)

    if proc.returncode != 0:
        emsg = proc.stderr.decode()
        msg = f'Failed to merge mbtiles. {emsg}'
        LOG.error(msg)

    for f in infiles:
        f.unlink()


def make_tile_config(work_dir: Path):

    out_dir = Path(work_dir, 'mbtiles')
    style_dir = Path(out_dir, 'styles')
    if not style_dir.exists():
        style_dir.mkdir()

    # collect inputs
    data = [p for p in out_dir.iterdir() if p.suffix == '.mbtiles']
    data.sort()
    bands = [int(f.stem[-1]) for f in data]
    styles = [p for p in Path('styles').iterdir() if p.suffix == '.json']

    # generate config
    cfg = json.load(Path('config_template.json').open('r'))
    cfg['data'].update({f.stem: {'mbtiles': f.name} for f in data})
    cfg['styles'].update(
        {f.stem: {'style': f.name, 'serve_data': True} for f in styles})
    json.dump(cfg, Path(out_dir, 'config.json').open('w'))

    # generate each style
    for style in styles:
        s: Dict = json.load(style.open('r'))
        s['sources'].update(
            {f.stem: {'type': 'vector', 'url': f'mbtiles://{f.name}'}
                for f in data}
        )
        # only edit layers where no source is defined
        layers = filter(
            lambda l: 'source-layer' in l and 'source' not in l, 
            s['layers']
        )
        s['layers'] = list(filter(
            lambda l: not ('source-layer' in l and 'source' not in l), 
            s['layers']
        ))

        for layer in layers:
            for d in data:
                band = int(d.stem[-1])
                newlayer = layer.copy()
                newlayer['id'] += '-' + d.stem
                newlayer['source'] = d.stem
                if 'maxzoom' not in newlayer:
                    if band < max(bands):
                        newlayer['maxzoom'] = BAND2ZOOM[band][1]
                if 'minzoom' not in newlayer:
                    if band > min(bands):
                        newlayer['minzoom'] = BAND2ZOOM[band][0]
                s['layers'].append(newlayer)
        json.dump(s, Path(out_dir, 'styles', style.name).open('w'))




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
    parser.add_argument('--geojson', action='store_true',
                        help='Generate intermediate GeoJSON representation')
    parser.add_argument('--tile', action='store_true',
                        help='Generate mbtiles')
    parser.add_argument('--style', action='store_true',
                        help='Generate tileserver configuration')
    parser.add_argument('--bb', nargs=4, type=float,
                        default=(-180.0, 180.0, -90.0, 90.0),
                        help='Bounding box for map min_lon max_lon min_lat, max_lat')

    args = parser.parse_args()

    if args.verbose:
        LOG.setLevel(logging.DEBUG)

    if args.get:
        LOG.debug('Downloading ENC files')
        get_enc(args.work_dir)

    encdir = Path(args.work_dir, 'ENC_ROOT')
    if not encdir.is_dir():
        LOG.error('ENC_ROOT does not exist. Should you use --get to download?')

    if args.geojson:
        make_geojsons(args.work_dir, args.bb, args.jobs)

    if args.tile:
        process_geojsons(args.work_dir, jobs=args.jobs)

    if args.style:
        make_tile_config(args.work_dir)

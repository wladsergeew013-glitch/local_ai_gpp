"""Run assertions once; tolerate only transient Windows directory removal 145.

Native libraries/AV can briefly retain a deleted directory entry. All other
cleanup errors, failed assertions, and exhausted retries remain test failures.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--start',default='tests');parser.add_argument('-v','--verbose',action='store_true');args=parser.parse_args()
    retries=0
    original=tempfile.TemporaryDirectory.cleanup
    temp_root=Path(tempfile.gettempdir()).resolve()
    def cleanup(directory):
        nonlocal retries
        target=Path(directory.name).resolve()
        for attempt in range(8):
            try:return original(directory)
            except OSError as error:
                if os.name!='nt' or error.winerror!=145 or not target.is_relative_to(temp_root) or target==temp_root or attempt==7:raise
                retries+=1;time.sleep(.1)
    if os.name=='nt':tempfile.TemporaryDirectory.cleanup=cleanup
    try:
        suite=unittest.defaultTestLoader.discover(args.start)
        result=unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    finally:tempfile.TemporaryDirectory.cleanup=original
    print('Windows temporary-directory cleanup retries:',retries)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())

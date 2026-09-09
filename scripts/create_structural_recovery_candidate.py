"""Create Candidate 181 (or verify original 180) through the normal configuration authority."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from src.backend.structural_recovery_candidate import create
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',type=int,choices=(180,181),default=181)
    candidate=create(parser.parse_args().revision)
    print(f"Candidate {candidate['candidate_revision']} ready: {candidate['label']}")
    print(f"ID: {candidate['candidate_id']}")
    print(f"Backtest: select Candidate {candidate['candidate_revision']} and a certified V6 book for the test ticker.")


if __name__=='__main__':
    main()

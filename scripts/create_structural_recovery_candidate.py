"""Create Candidate 180 through the normal configuration authority."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from src.backend.structural_recovery_candidate import create
    candidate=create()
    print(f"Candidate {candidate['candidate_revision']} ready: {candidate['label']}")
    print(f"ID: {candidate['candidate_id']}")
    print('Backtest: select Candidate 180 and a certified V6 book for the test ticker.')


if __name__=='__main__':
    main()

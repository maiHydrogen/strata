"""Write the STAAD spike input: a one-bay x one-bay G+1 RCC frame in Guwahati.

Run: python scripts/make_std_spike.py
Then open output/spike/frame_g1.std in STAAD.Pro and run the analysis.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.structure.std_writer import make_rc_frame, write_std  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "spike", "frame_g1.std")

if __name__ == "__main__":
    frame = make_rc_frame(bays_x=[4.0], bays_z=[4.0], storeys=2, storey_height=3.0)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(write_std(frame, title="STRATA spike G+1 one-bay frame"))
    print(OUT)

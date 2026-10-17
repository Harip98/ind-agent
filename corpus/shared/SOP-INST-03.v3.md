---
doc_id: SOP-INST-03
version: 3
doc_type: sop
effective_date: 2026-01-20
superseded_by: null
tenant: _shared
asset_models: [CP-200, CL-50, AC-75]
license: self-authored
---
# Temperature Transmitter Verification and Calibration

## 1 Scope

Applies to RTD temperature transmitters on pumps, cooling loops and compressors, including TT-101, TT-102, TT-201, TT-202 and TT-301.

## 2 When to Suspect the Instrument

Suspect an instrument fault when a single temperature rises steadily or jumps while every related process signal stays flat (flow, pressure, current, vibration and neighbouring temperatures). Physical heating of equipment almost always shows in at least one related signal. Also suspect the instrument when the signal flat-lines (identical values for a long period) or is reported with bad quality.

## 3 Bad Quality and Flat-Line Signals

A flat-line followed by missing data or a bad-quality status usually indicates a failed transmitter, a broken RTD element, a loose terminal or a failed input card. Treat the reading as unknown: do not use it to confirm or exclude a process problem. Check the transmitter power, the loop current (4–20 mA) and the input card status.

## 4 Field Verification

Compare the transmitter reading with a calibrated portable reference thermometer placed at the same measurement point. A difference above 2 °C confirms drift. Record both readings in the instrument log.

## 5 Calibration and Replacement

Recalibrate in the workshop using a dry-block calibrator at three points across the range. Replace the RTD element if drift exceeds 5 °C or if the element resistance is out of tolerance. After replacement, verify the alarm and trip functions.

# Changelog

## 0.2.0

- Name packet sizes, FIFO tokens, header fields and driver timing budgets.
- Decode the single status/IRQ burst with generated register fieldsets.
- Share initialization and CC configuration across the existing constructors.
- Separate I2C transport and packet coding, and remove unused formatting macros.
- Preserve one FIFO read per control frame and two per data frame, including
  GoodCRC filtering and hardware retry handling.
- Add regression coverage for all object counts, TX wire coding, CC orientation
  and initialization errors; add CI for feature combinations, embedded targets,
  documentation and the packaged crate.
- Document the datasheet/specification decisions and remove the obsolete TODO.

### Breaking changes

- `FusbError` adds `UnsupportedVersion(u8)` and `NoCcDetected`; exhaustive matches
  need to cover these variants.
- Constructors reject a missing CC source instead of silently selecting CC2.

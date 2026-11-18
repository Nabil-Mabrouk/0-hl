// T-M1.5-01 — formatting: exact decimal rounding, "—" for unknown, uptime, percents.
import { describe, expect, it } from "vitest";
import { DASH, fmtNum, fmtPct, fmtSignedUsd, fmtTime, fmtUptime, fmtUsd, roundDecimal, symbolOf } from "./format";

describe("format", () => {
  it("rounds decimal strings exactly (no float)", () => {
    expect(roundDecimal("10745.214999", 2)).toBe("10745.21");
    expect(roundDecimal("0.005", 2)).toBe("0.01");
    expect(roundDecimal("-0.004", 2)).toBe("0.00");
    expect(roundDecimal("123456789012345678901.995", 2)).toBe("123456789012345678902.00");
    expect(roundDecimal("abc", 2)).toBeNull();
  });

  it("formats money and signs", () => {
    expect(fmtUsd("10745.214999")).toBe("$10,745.21");
    expect(fmtUsd("-348.584")).toBe("-$348.58");
    expect(fmtSignedUsd("342.52")).toBe("+$342.52");
    expect(fmtSignedUsd("-1")).toBe("-$1.00");
  });

  it("never turns unknown into zero", () => {
    expect(fmtUsd(null)).toBe(DASH);
    expect(fmtNum(undefined)).toBe(DASH);
    expect(fmtPct(null)).toBe(DASH);
    expect(fmtUptime(null)).toBe(DASH);
    expect(fmtTime(null)).toBe(DASH);
  });

  it("formats numbers, uptime, time and symbols", () => {
    expect(fmtNum("0.000003767000000000")).toBe("0.000003767");
    expect(fmtNum("101.000000000000000000")).toBe("101");
    expect(fmtUptime(23913)).toBe("06:38:33");
    expect(fmtTime("2026-11-18T06:34:00Z")).toBe("06:34");
    expect(fmtPct(27.84, 1, true)).toBe("+27.8%");
    expect(symbolOf("kraken_spot:BTC/USD")).toBe("$BTC");
  });
});

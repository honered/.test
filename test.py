def earthquake_emoji(magnitude: float) -> str:
    """
       Returns a relevant emoji based on earthquake magnitude severity.
       Only uses: ❓🟢🟡🟠🔴🌋🌎💥🌊

       Severity scale:
       < 2.0   → Micro (not felt)             🟢
       2.0–3.9   → Minor (rarely felt)          🟡
    4.0–4.9   → Light (noticeable shaking)   🟠
    5.0–5.9   → Moderate (some damage)       🔴
    6.0–6.9   → Strong (destructive)         💥
    7.0–7.9   → Major (widespread damage)    🌋
    8.0–8.9   → Great (devastating)          🌎💥
      ≥ 9.0   → Rare/Epic (catastrophic)     🌎💥🌊
       < 0    → Invalid                        ❓
    """
    if magnitude < 0:
        return "❓"
    elif magnitude < 2.0:
        return "🟢"  # Barely felt or not felt
    elif magnitude < 4.0:
        return "🟡"  # Minor, usually no damage
    elif magnitude < 5.0:
        return "🟠"  # Felt by most, light shaking
    elif magnitude < 6.0:
        return "🔴"  # Moderate – can cause damage to weak buildings
    elif magnitude < 7.0:
        return "💥"  # Strong – destructive in populated areas
    elif magnitude < 8.0:
        return "🌋"  # Major – serious damage over large areas
    elif magnitude < 9.0:
        return "🌎💥"  # Great – devastating, near total destruction
    else:
        return "🌎💥🌊"  # Extremely rare (like 1960 Chile 9.5) – can cause tsunamis


# Quick test
if __name__ == "__main__":
    tests = [1.2, 3.5, 4.8, 5.7, 6.4, 7.8, 8.3, 9.5, -1]
    for m in tests:
        print(f"{m} : {earthquake_emoji(m)}")

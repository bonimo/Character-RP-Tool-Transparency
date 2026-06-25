import re

LABELS = ["USER'S WHY", "TOUCHED", "APPRAISAL", "TENSION LEVEL", "TENSION",
          "GIVEN", "TRANSFORMATION", "EFFECTIVE", "ACTION TENDENCY", "AGENDA",
          "COURSE A", "COURSE B", "CHOSEN MOVE", "INITIATIVE", "CONNECTION",
          "EMOTIONAL STATE"]

def strip_think(t):
    return re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL).strip()

def parse_appraisal(text):
    text = strip_think(text)
    fields, current = {}, None
    labels_sorted = sorted(LABELS, key=len, reverse=True)  # TENSION LEVEL before TENSION
    for raw in text.splitlines():
        line = raw.strip()
        matched = None
        for lab in labels_sorted:
            if line.upper().startswith(lab + ":"):
                matched = lab
                break
        if matched:
            current = matched
            fields[current] = line[len(matched) + 1:].strip()
        elif current and line:
            fields[current] += " " + line

    def g(lab): return fields.get(lab, "").strip()
    def pick(s, opts): return next((o for o in opts if o in s.lower()), "")

    return {
        "users_why":      g("USER'S WHY"),
        "touched":        g("TOUCHED"),
        "appraisal":      g("APPRAISAL"),
        "tension_level":  pick(g("TENSION LEVEL"), ["none", "mild", "moderate", "strong"]),
        "tension":        g("TENSION"),
        "given":          g("GIVEN"),
        "transformation": g("TRANSFORMATION"),
        "effective":      g("EFFECTIVE"),
        "action_tendency":g("ACTION TENDENCY"),
        "agenda":         g("AGENDA"),
        "course_a":       g("COURSE A"),
        "course_b":       g("COURSE B"),
        "chosen_move":    g("CHOSEN MOVE"),
        "initiative":     pick(g("INITIATIVE"), ["yield", "nudge", "lead"]),
        "connection":     pick(g("CONNECTION"), ["connect", "resist", "conflicted"]),
        "emotional_state":g("EMOTIONAL STATE"),
    }

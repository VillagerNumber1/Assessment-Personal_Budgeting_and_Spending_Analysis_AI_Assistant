lines = []

def add(text: str) -> int:
    lines.append(text)
    return len(lines)

def all_lines() -> list[str]:
    return lines
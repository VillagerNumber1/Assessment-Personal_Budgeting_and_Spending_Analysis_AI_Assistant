lines = []

while True:
    text = input("记账> ").strip()
    if text.lower() in {"q", "quit"}:
        break
    if not text:
        continue
    lines.append(text)
    print(f"[{len(lines)}] 收到：{text}")

print(f"\n共 {len(lines)} 条：")
for i, t in enumerate(lines, 1):
    print(f"  {i}. {t}")

input("\n按回车退出…")

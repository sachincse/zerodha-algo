"""Inject freshly generated SVG charts into the report template."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "out", "charts.json"), encoding="utf-8"))
h = open(os.path.join(HERE, "report.template.html"), encoding="utf-8").read()
h = h.replace("<!--EQUITY_SVG-->", d["equity"]).replace("<!--DD_SVG-->", d["drawdown"])
p = os.path.join(HERE, "out", "report.html")
open(p, "w", encoding="utf-8").write(h)
print("built", p, len(h), "chars")

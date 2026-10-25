#!/usr/bin/env python3
"""gen_vacuity.py - add an antecedent cover next to every implication assertion in a checker file.

    gen_vacuity.py <checkers.sv> <out.sv>

An assertion `a_x: assert property (@(posedge clk) disable iff (D) ANTE |-> CONS);` passes vacuously when ANTE never holds. For every such
assertion this script writes the same file with one extra line after it,

    c_ante_x: cover property (@(posedge clk) disable iff (D) ANTE);

so a simulation reports how many times each assertion's precondition was actually met. An assertion whose `c_ante_*` bin is never hit has
not been exercised by that simulation, whatever it printed. Assertions without an implication (plain equalities, evaluated every cycle) are
not vacuous and get no extra cover; the script lists them so the count of "conditional" and "unconditional" assertions is explicit.
Only single-line `label: assert property (...);` statements are rewritten; procedural scoreboard asserts are left alone.
"""
import re
import sys


def matching(s, i):
    """index of the ')' that closes the '(' at s[i]"""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("unbalanced parentheses: " + s)


def split_implication(body):
    depth = 0
    for k in range(len(body) - 2):
        c = body[k]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and body[k:k + 3] in ("|->", "|=>"):
            return body[:k].strip(), body[k:k + 3]
    return None, None


def main():
    src, dst = sys.argv[1], sys.argv[2]
    out, cond, uncond = [], [], []
    pat = re.compile(r"^(\s*)(a_\w+):\s+assert property \(\s*@\(posedge clk\)\s*(.*)\);(\s*//.*)?\s*$")
    for line in open(src).read().splitlines():
        out.append(line)
        m = pat.match(line)
        if not m:
            continue
        indent, name, rest = m.group(1), m.group(2), m.group(3)
        dis = ""
        if rest.startswith("disable iff"):
            op = rest.index("(")
            cl = matching(rest, op)
            dis = rest[:cl + 1] + " "
            rest = rest[cl + 1:].strip()
        ante, _ = split_implication(rest)
        if ante is None:
            uncond.append(name)
            continue
        cond.append(name)
        out.append("%sc_ante_%s: cover property (@(posedge clk) %s%s);" % (indent, name[2:], dis, ante))
    open(dst, "w").write("\n".join(out) + "\n")
    print("gen_vacuity: %d conditional assertions got an antecedent cover; %d unconditional (never vacuous): %s" % (
        len(cond), len(uncond), ", ".join(uncond) or "none"))


if __name__ == "__main__":
    main()

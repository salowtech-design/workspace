/* Salow Sheets formula engine. Runs on the device (browser or node); it reads only the cells it is
 * given. No eval, no Function, no network. Formulas are parsed by a hand-written recursive-descent
 * parser into a tree, then walked. Errors are values: #DIV/0!, #VALUE!, #NAME?, #REF!, #CIRC!, #PARSE!, #N/A (a lookup found nothing). */
(function (root) {
  "use strict";
  var MAX_LEN = 2000, MAX_DEPTH = 50, MAX_RANGE = 10000, MAX_ROW = 1000, MAX_COL = 26, MAX_STR = 10000, BUDGET = 500000, MAX_CELL_TEXT = 10000, MAX_DEPTH_EVAL = 400;

  function Err(code) { this.code = code; }
  Err.prototype.toString = function () { return this.code; };
  var isErr = function (v) { return v instanceof Err; };

  function colNum(letters) { var n = 0; for (var i = 0; i < letters.length; i++) n = n * 26 + letters.charCodeAt(i) - 64; return n; }
  function addr(col, row) { return String.fromCharCode(64 + col) + row; }
  function parseAddr(s) {
    var m = /^([A-Z]{1,2})([1-9][0-9]{0,3})$/.exec(s);
    if (!m) return null;
    var c = colNum(m[1]), r = parseInt(m[2], 10);
    if (c > MAX_COL || r > MAX_ROW) return null;
    return { col: c, row: r };
  }

  function tokenize(src) {
    var t = [], i = 0, m;
    while (i < src.length) {
      var ch = src[i];
      if (/\s/.test(ch)) { i++; continue; }
      if ((m = /^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?|^\.[0-9]+/.exec(src.slice(i)))) { var nv = parseFloat(m[0]); if (!isFinite(nv)) throw new Err("#VALUE!"); t.push({ k: "num", v: nv }); i += m[0].length; continue; }
      if (ch === '"') {
        var j = i + 1, s = "";
        while (j < src.length) {
          if (src[j] === '"') { if (src[j + 1] === '"') { s += '"'; j += 2; continue; } break; }
          s += src[j++];
        }
        if (j >= src.length) throw new Err("#PARSE!");
        t.push({ k: "str", v: s }); i = j + 1; continue;
      }
      if ((m = /^[A-Za-z_][A-Za-z0-9_.]*/.exec(src.slice(i)))) { t.push({ k: "id", v: m[0].toUpperCase() }); i += m[0].length; continue; }
      if ((m = /^(<=|>=|<>|[-+*\/^&=<>(),:%])/.exec(src.slice(i)))) { t.push({ k: "op", v: m[0] }); i += m[0].length; continue; }
      throw new Err("#PARSE!");
    }
    return t;
  }

  // precedence low to high: comparison, concat, add, mul, pow, unary, percent, primary
  function parse(src) {
    if (typeof src !== "string" || src.length > MAX_LEN) throw new Err("#PARSE!");
    var t = tokenize(src), p = 0, depth = 0;
    var peek = function () { return t[p]; };
    var isOp = function (v) { return t[p] && t[p].k === "op" && t[p].v === v; };
    function enter() { if (++depth > MAX_DEPTH) throw new Err("#PARSE!"); }
    function cmp() {
      var l = cat();
      while (t[p] && t[p].k === "op" && /^(=|<>|<=|>=|<|>)$/.test(t[p].v)) { var o = t[p++].v; l = { n: "bin", o: o, l: l, r: cat() }; }
      return l;
    }
    function cat() { var l = add(); while (isOp("&")) { p++; l = { n: "bin", o: "&", l: l, r: add() }; } return l; }
    function add() { var l = mul(); while (isOp("+") || isOp("-")) { var o = t[p++].v; l = { n: "bin", o: o, l: l, r: mul() }; } return l; }
    function mul() { var l = pow(); while (isOp("*") || isOp("/")) { var o = t[p++].v; l = { n: "bin", o: o, l: l, r: pow() }; } return l; }
    function pow() { var l = unary(); while (isOp("^")) { p++; l = { n: "bin", o: "^", l: l, r: unary() }; } return l; }
    function unary() {
      if (isOp("-") || isOp("+")) { enter(); var o = t[p++].v; var e = { n: "un", o: o, e: unary() }; depth--; return e; }
      return pct();
    }
    function pct() { var e = primary(); while (isOp("%")) { p++; e = { n: "bin", o: "/", l: e, r: { n: "lit", v: 100 } }; } return e; }
    function primary() {
      var x = t[p++];
      if (!x) throw new Err("#PARSE!");
      if (x.k === "num" || x.k === "str") return { n: "lit", v: x.v };
      if (x.k === "op" && x.v === "(") { enter(); var e = cmp(); if (!isOp(")")) throw new Err("#PARSE!"); p++; depth--; return e; }
      if (x.k === "id") {
        if (isOp("(")) {
          p++; enter(); var args = [];
          if (!isOp(")")) { for (;;) { args.push(cmp()); if (isOp(",")) { p++; continue; } break; } }
          if (!isOp(")")) throw new Err("#PARSE!");
          p++; depth--; return { n: "call", f: x.v, a: args };
        }
        if (x.v === "TRUE" || x.v === "FALSE") return { n: "lit", v: x.v === "TRUE" };
        var a = parseAddr(x.v);
        if (!a) {
          if (/^[A-Z]{1,3}[0-9]+$/.test(x.v)) return { n: "ref_err" };  // looks like a cell, out of range
          throw new Err("#NAME?");
        }
        if (isOp(":")) {
          p++; var y = t[p++];
          var b = y && y.k === "id" ? parseAddr(y.v) : null;
          if (!b) throw new Err("#REF!");
          return { n: "range", a: a, b: b };
        }
        return { n: "cell", a: a };
      }
      throw new Err("#PARSE!");
    }
    var tree = cmp();
    if (p !== t.length) throw new Err("#PARSE!");
    return tree;
  }

  function num(v) {
    if (isErr(v)) return v;
    if (v === null || v === undefined || v === "") return 0;
    if (typeof v === "boolean") return v ? 1 : 0;
    if (typeof v === "number") return v;
    var s = String(v).trim();
    if (/^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s) && isFinite(parseFloat(s))) return parseFloat(s);
    return new Err("#VALUE!");
  }
  function text(v) {
    if (isErr(v)) return v;
    if (v === null || v === undefined) return "";
    if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
    return String(v);
  }
  function flatten(args, out) {  // numbers from ranges ignore text and blanks, direct args must be numeric
    var nums = [];
    for (var i = 0; i < args.length; i++) {
      var a = args[i];
      if (Array.isArray(a)) {
        for (var j = 0; j < a.length; j++) {
          if (isErr(a[j])) return a[j];
          if (typeof a[j] === "number") nums.push(a[j]);
        }
      } else { var n = num(a); if (isErr(n)) return n; nums.push(n); }
    }
    return nums;
  }
  function roundHalfAway(x, d) {  // decimal-string shift, so 2.4999999995 -> 2 and 1.005 -> 1.01
    var ax = Math.abs(x), str = String(ax), f = Math.pow(10, d), out;
    if (str.indexOf("e") >= 0) { out = Math.round(ax * f) / f; return isFinite(out) ? (x < 0 ? -out : out) : x; }
    if (ax * f >= 9007199254740992) return x;  // no fractional digit left at this scale
    var up = Math.round(Number(str + "e" + d));
    out = Number(up + "e" + (-d));
    if (!isFinite(out)) return x;
    return x < 0 ? -out : out;
  }
  var FNX, OWN = Object.prototype.hasOwnProperty;
  var FN = {
    SUM: function (a) { var n = flatten(a); return isErr(n) ? n : n.reduce(function (s, x) { return s + x; }, 0); },
    AVERAGE: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? n.reduce(function (s, x) { return s + x; }, 0) / n.length : new Err("#DIV/0!"); },
    MIN: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? Math.min.apply(null, n) : 0; },
    MAX: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? Math.max.apply(null, n) : 0; },
    COUNT: function (a) { var c = 0; a.forEach(function (x) { if (Array.isArray(x)) x.forEach(function (y) { if (typeof y === "number") c++; }); else if (!isErr(num(x))) c++; }); return c; },
    ABS: function (a) { var x = num(a[0]); return isErr(x) ? x : Math.abs(x); },
    ROUND: function (a) { var x = num(a[0]), d = a.length > 1 ? num(a[1]) : 0; if (isErr(x)) return x; if (isErr(d)) return d; return roundHalfAway(x, Math.max(-15, Math.min(15, Math.trunc(d)))); },
    LEN: function (a) { var s = text(a[0]); return isErr(s) ? s : s.length; },
    UPPER: function (a) { var s = text(a[0]); return isErr(s) ? s : s.toUpperCase().slice(0, MAX_STR); },
    LOWER: function (a) { var s = text(a[0]); return isErr(s) ? s : s.toLowerCase(); },
    CONCAT: function (a) { var o = ""; for (var i = 0; i < a.length; i++) { var s = text(a[i]); if (isErr(s)) return s; o += s; if (o.length > MAX_STR) return new Err("#VALUE!"); } return o; },
    NOT: function (a) { var x = truth(a[0]); return isErr(x) ? x : !x; },
    AND: function (a) { for (var i = 0; i < a.length; i++) { var x = truth(a[i]); if (isErr(x)) return x; if (!x) return false; } return true; },
    OR: function (a) { for (var i = 0; i < a.length; i++) { var x = truth(a[i]); if (isErr(x)) return x; if (x) return true; } return false; }
  };

  /* ---- lookup and conditional functions ----
   * Written down here so they are exact. The Google Docs Editors help pages for INDEX, MATCH, VLOOKUP, SUMIF,
   * COUNTIF and AVERAGEIF were read for these functions; they do not say everything below (edge cases such as
   * the sum range shape, error cells, blanks and type mixing are this engine's own choices, not checked against
   * Google's or Excel's engine):
   * - Indices are 1-based. INDEX row or column 0 (a whole row or column) is not supported: #VALUE!. One index is the row, as in Google; on a range
   *   wider than one column it would mean a whole row, so it is #VALUE!.
   * - MATCH search_type: positive = 1 (default, ascending, largest <= key), 0 = exact, negative = -1
   *   (descending, smallest >= key). A range taller and wider than 1 gives #N/A. VLOOKUP is_sorted
   *   defaults to TRUE (approximate, ascending first column); FALSE is exact. Approximate modes scan
   *   top to bottom and stop at the first value past the key, like a sorted search, and skip blanks
   *   and values of another type than the key (numbers, text and booleans are not compared with each other).
   * - Exact text matches ignore case and accept the wildcards ? and * (~ escapes ? * and ~), also in
   *   COUNTIF, SUMIF and AVERAGEIF text criteria. A text key never equals a number cell and a number never equals a text cell
   *   in MATCH and VLOOKUP; the *IF functions also let a numeric string criterion match text of the same digits.
   * - Blank key: #N/A. An error value in the key is returned. An error cell in the searched range is skipped
   *   (never matches); an error in a returned cell is returned. Nothing found: #N/A. INDEX or VLOOKUP column
   *   past the range: #REF!. VLOOKUP index below 1 or not a number: #VALUE!.
   * - SUMIF and AVERAGEIF need a sum range of the same height and width as the range (it is not resized
   *   as Google and Excel do): otherwise #VALUE!. Only numbers are summed; text and blanks are ignored.
   * - Ragged data does not exist: a range is a full rectangle of cells, empty cells are blank. */
  function grid(v) { return Array.isArray(v) ? { v: v, rows: v.rows || v.length, cols: v.cols || 1 } : { v: [v], rows: 1, cols: 1 }; }
  function isBlank(x) { return x === null || x === undefined || x === ""; }
  var meter = null;  // set by the evaluator before each function call: charges comparison work to the sheet budget
  function wild(pat, str) {  // ? one character, * any run, ~ escapes; case-insensitive; no backtracking blow-up
    var toks = [], i;
    for (i = 0; i < pat.length; i++) {
      var ch = pat[i];
      if (ch === "~" && i + 1 < pat.length && (pat[i + 1] === "?" || pat[i + 1] === "*" || pat[i + 1] === "~")) { toks.push({ t: pat[++i] }); }
      else if (ch === "*") { if (!toks.length || toks[toks.length - 1].star !== true) toks.push({ star: true }); }
      else if (ch === "?") toks.push({ any: true });
      else toks.push({ t: ch });
    }
    if (meter && meter(Math.ceil(toks.length * (str.length + 1) / 50))) throw new Err("#LIMIT!");  // worst case of the scan below, charged at 1 unit per 50 steps (a sheet may do about 25 million)
    var sp = 0, tp = 0, star = -1, mark = 0;
    str = str.toLowerCase();
    var lit = function (k) { return String(toks[k].t).toLowerCase(); };
    while (sp < str.length) {
      if (tp < toks.length && !toks[tp].star && (toks[tp].any || lit(tp) === str[sp])) { sp++; tp++; }
      else if (tp < toks.length && toks[tp].star) { star = tp++; mark = sp; }
      else if (star >= 0) { tp = star + 1; sp = ++mark; }
      else return false;
    }
    while (tp < toks.length && toks[tp].star) tp++;
    return tp === toks.length;
  }
  function kind(x) { return typeof x === "number" ? "n" : typeof x === "boolean" ? "b" : typeof x === "string" && x !== "" ? "t" : null; }
  function cmpSame(a, b) {  // a and b are the same kind; -1, 0, 1
    if (typeof a === "string") { a = a.toLowerCase(); b = b.toLowerCase(); }
    return a < b ? -1 : a > b ? 1 : 0;
  }
  function wildTooLong() { throw new Err("#VALUE!"); }  // a text key over 255 characters, as for criteria
  function exactEq(key, cell) {
    var k = kind(key);
    if (k === null || k !== kind(cell)) return false;
    if (k === "t") return key.length > 255 ? wildTooLong() : wild(key, cell);
    return key === cell;
  }
  function criterion(c) {  // returns a predicate over one cell value, or an Err
    if (isErr(c)) return c;
    if (Array.isArray(c)) return new Err("#VALUE!");
    if (typeof c === "number") return function (x) { return typeof x === "number" && x === c; };
    if (typeof c === "boolean") return function (x) { return typeof x === "boolean" && x === c; };
    var s = text(c);
    if (s.length > 255) return new Err("#VALUE!");
    var op = "=", m = /^(>=|<=|<>|=|>|<)/.exec(s);
    if (m) { op = m[1]; s = s.slice(op.length); }
    var numeric = /^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s.trim()) && isFinite(parseFloat(s)), nv = numeric ? parseFloat(s) : 0;
    var low = s.toLowerCase();
    if (op === ">" || op === "<" || op === ">=" || op === "<=") {
      return function (x) {
        var c2;
        if (numeric) { if (typeof x !== "number") return false; c2 = x < nv ? -1 : x > nv ? 1 : 0; }
        else { if (kind(x) !== "t") return false; var xl = x.toLowerCase(); c2 = xl < low ? -1 : xl > low ? 1 : 0; }
        return op === ">" ? c2 > 0 : op === "<" ? c2 < 0 : op === ">=" ? c2 >= 0 : c2 <= 0;
      };
    }
    var eq;
    if (s === "") eq = function (x) { return isBlank(x); };
    else if (numeric) eq = function (x) { return (typeof x === "number" && x === nv) || (kind(x) === "t" && x.trim() === s.trim()); };
    else if (low === "true" || low === "false") eq = function (x) { return typeof x === "boolean" && x === (low === "true"); };
    else eq = function (x) { return kind(x) === "t" && wild(s, x); };
    return op === "<>" ? function (x) { return !eq(x); } : eq;
  }
  function pos(v) {  // a positive whole number from an argument, or an Err
    var n = num(sc2(v)); if (isErr(n)) return n;
    n = Math.trunc(n);
    return n < 1 ? new Err("#VALUE!") : n;
  }
  function sc2(v) { return Array.isArray(v) ? (v.length === 1 ? v[0] : new Err("#VALUE!")) : v; }
  var LOOKUP = {
    INDEX: function (a) {
      if (isErr(a[0])) return a[0];
      var g = grid(a[0]), r = pos(a[1]); if (isErr(r)) return r;
      var c = a.length > 2 ? pos(a[2]) : null; if (isErr(c)) return c;
      if (c === null) { if (g.cols > 1) return new Err("#VALUE!"); c = 1; }  // Google reads one index as the row and an omitted column as 0, a whole row; whole rows are not supported
      if (r > g.rows || c > g.cols) return new Err("#REF!");
      var v = g.v[(r - 1) * g.cols + (c - 1)];
      return v === undefined ? null : v;
    },
    MATCH: function (a) {
      var key = sc2(a[0]); if (isErr(key)) return key;
      if (isErr(a[1])) return a[1];
      var g = grid(a[1]); if (g.rows > 1 && g.cols > 1) return new Err("#N/A");
      var t = 1;
      if (a.length > 2) { t = num(sc2(a[2])); if (isErr(t)) return t; t = t > 0 ? 1 : t < 0 ? -1 : 0; }
      var i = find(key, g.v, 0, 1, g.v.length, t);
      return isErr(i) ? i : i + 1;
    },
    VLOOKUP: function (a) {
      var key = sc2(a[0]); if (isErr(key)) return key;
      if (isErr(a[1])) return a[1];
      var g = grid(a[1]), ci = pos(a[2]); if (isErr(ci)) return ci;
      var sorted = true;
      if (a.length > 3) { sorted = truth(sc2(a[3])); if (isErr(sorted)) return sorted; }
      if (ci > g.cols) return new Err("#REF!");
      var i = find(key, g.v, 0, g.cols, g.rows, sorted ? 1 : 0);
      if (isErr(i)) return i;
      var v = g.v[i * g.cols + (ci - 1)];
      return v === undefined ? null : v;
    }
  };
  function find(key, vals, start, step, count, type) {  // index of the match among count cells, or #N/A
    if (kind(key) === null) return new Err("#N/A");
    var best = -1;
    for (var i = 0; i < count; i++) {
      var x = vals[start + i * step];
      if (isErr(x) || kind(x) !== kind(key)) continue;
      if (type === 0) { if (exactEq(key, x)) return i; continue; }
      var d = cmpSame(x, key);
      if (type === 1) { if (d > 0) break; best = i; }
      else { if (d < 0) break; best = i; }
    }
    return best < 0 ? new Err("#N/A") : best;
  }
  function condAgg(name, a) {
    if (isErr(a[0])) return a[0];
    var g = grid(a[0]), pred = criterion(a[1]); if (isErr(pred)) return pred;
    var sg = g;
    if (name !== "COUNTIF" && a.length > 2) { if (isErr(a[2])) return a[2]; sg = grid(a[2]); if (sg.rows !== g.rows || sg.cols !== g.cols) return new Err("#VALUE!"); }
    var count = 0, sum = 0;
    for (var i = 0; i < g.v.length; i++) {
      var x = g.v[i];
      if (isErr(x) || !pred(x)) continue;
      count++;
      if (name !== "COUNTIF") { var y = sg.v[i]; if (isErr(y)) return y; if (typeof y === "number") { sum += y; } else if (name === "AVERAGEIF") count--; }
    }
    if (name === "COUNTIF") return count;
    if (name === "SUMIF") return sum;
    return count ? sum / count : new Err("#DIV/0!");
  }
  FNX = { INDEX: LOOKUP.INDEX, MATCH: LOOKUP.MATCH, VLOOKUP: LOOKUP.VLOOKUP,
    SUMIF: function (a) { return condAgg("SUMIF", a); }, COUNTIF: function (a) { return condAgg("COUNTIF", a); }, AVERAGEIF: function (a) { return condAgg("AVERAGEIF", a); } };

  /* ---- text functions (Google Sheets help semantics, not its engine) ----
   * Google's help pages for LEFT, RIGHT, MID, TRIM, SUBSTITUTE and FIND were read. They cover the defaults, FIND's case
   * sensitivity and #VALUE! when not found, and TRIM keeping non-breaking spaces. Negative counts, an empty search text,
   * a FIND start past the end, and SUBSTITUTE being case-sensitive with no wildcards are this engine's choices.
   * Counts are whole numbers of UTF-16 units, like LEN: an emoji counts 2. A count below 0 is #VALUE!; a
   * fraction is cut toward 0. LEFT and RIGHT default to 1. MID start is 1-based, below 1 is #VALUE!, past the
   * end gives "". TRIM removes leading and trailing spaces and collapses runs of spaces (only the space
   * character). SUBSTITUTE is case-sensitive and does not use wildcards; an empty old text changes nothing; the
   * optional instance picks the nth occurrence (below 1 is #VALUE!). FIND is case-sensitive, has no wildcards,
   * start is 1-based, an empty search text gives start, and not found is #VALUE! (as in Sheets). */
  function cnt(v, dflt) { if (v === undefined) return dflt; var n = num(sc2(v)); if (isErr(n)) return n; n = Math.trunc(n); return n < 0 ? new Err("#VALUE!") : n; }
  function tx(v) { return text(sc2(v)); }
  FNX.LEFT = function (a) { var s = tx(a[0]), n = cnt(a[1], 1); return isErr(s) ? s : isErr(n) ? n : s.slice(0, n); };
  FNX.RIGHT = function (a) { var s = tx(a[0]), n = cnt(a[1], 1); return isErr(s) ? s : isErr(n) ? n : n === 0 ? "" : s.slice(-n); };
  FNX.MID = function (a) {
    var s = tx(a[0]), st = num(sc2(a[1])), n = cnt(a[2], 0);
    if (isErr(s)) return s; if (isErr(st)) return st; if (isErr(n)) return n;
    st = Math.trunc(st); if (st < 1) return new Err("#VALUE!");
    return s.substr(st - 1, n);
  };
  FNX.TRIM = function (a) { var s = tx(a[0]); return isErr(s) ? s : s.replace(/ +/g, " ").replace(/^ | $/g, ""); };
  FNX.SUBSTITUTE = function (a) {
    var s = tx(a[0]), o = tx(a[1]), r = tx(a[2]);
    if (isErr(s)) return s; if (isErr(o)) return o; if (isErr(r)) return r;
    var inst = null;
    if (a.length > 3) { inst = num(sc2(a[3])); if (isErr(inst)) return inst; inst = Math.trunc(inst); if (inst < 1) return new Err("#VALUE!"); }
    if (o === "") return s;
    var out = "", i = 0, k = 0, j;
    while ((j = s.indexOf(o, i)) >= 0) {
      k++;
      if (inst === null || k === inst) { out += s.slice(i, j) + r; } else { out += s.slice(i, j + o.length); }
      i = j + o.length;
      if (inst !== null && k === inst) break;
      if (out.length > MAX_STR) return new Err("#VALUE!");
    }
    out += s.slice(i);
    return out.length > MAX_STR ? new Err("#VALUE!") : out;
  };
  FNX.FIND = function (a) {
    var f = tx(a[0]), s = tx(a[1]), st = a.length > 2 ? num(sc2(a[2])) : 1;
    if (isErr(f)) return f; if (isErr(s)) return s; if (isErr(st)) return st;
    st = Math.trunc(st); if (st < 1 || st > s.length + 1) return new Err("#VALUE!");
    var j = s.indexOf(f, st - 1);
    return j < 0 ? new Err("#VALUE!") : j + 1;
  };

  /* ---- date functions on serial numbers (deterministic: no TODAY, NOW or clock) ----
   * The Google Docs Editors help pages for DATE, YEAR, MONTH, DAY and WEEKDAY were read. Serial 0 is 1899-12-30 and
   * serial 1 is 1899-12-31, so DATE(1900,1,1) is 2 and DATE(1970,1,1) is 25569. A fraction (a time of day) is dropped
   * by rounding the serial down. All arithmetic is UTC, so the browser's time zone and daylight saving never matter.
   * DATE: year, month and day must be numbers (text is #VALUE!, unlike the numeric-text coercion elsewhere in this engine);
   * decimals are cut toward 0; month and day outside their range roll over (DATE(1969,13,1) is 1970-01-01); a year
   * 0 to 1899 has 1900 added; a year below 0, or a result outside the years 0 to 9999, is #NUM!. YEAR, MONTH and DAY
   * take a serial number (the engine has no date type, and a typed date string is just text, as in Google's help);
   * a serial outside the years 0 to 9999 is #NUM!. WEEKDAY types 1, 2, 3 and 11 to 17 as in Google's help; any other
   * type is #NUM!. #NUM! is a new error code here. Not built: DATEVALUE, EDATE, EOMONTH, DATEDIF, TEXT and date formats. */
  var DAY_MS = 86400000, BASE_MS = Date.UTC(1899, 11, 30);
  function serialDate(n) {
    var d = Math.floor(n), t = BASE_MS + d * DAY_MS, dt = new Date(t);
    if (!isFinite(t) || isNaN(dt.getTime())) return null;
    var y = dt.getUTCFullYear();
    return y < 0 || y > 9999 ? null : dt;
  }
  function dnum(v) { v = sc2(v); if (isErr(v)) return v; if (typeof v === "string" && v !== "") return new Err("#VALUE!"); var n = num(v); return n; }
  function dpart(a, pick) { var n = dnum(a[0]); if (isErr(n)) return n; var dt = serialDate(n); return dt ? pick(dt) : new Err("#NUM!"); }
  FNX.DATE = function (a) {
    var y = dnum(a[0]), m = dnum(a[1]), d = dnum(a[2]);
    if (isErr(y)) return y; if (isErr(m)) return m; if (isErr(d)) return d;
    y = Math.trunc(y); m = Math.trunc(m); d = Math.trunc(d);
    if (y < 0 || y > 9999) return new Err("#NUM!");
    if (y < 1900) y += 1900;
    if (Math.abs(m) > 120000 || Math.abs(d) > 4000000) return new Err("#NUM!");
    var dt = new Date(0); dt.setUTCFullYear(y, m - 1, d); dt.setUTCHours(0, 0, 0, 0);
    var yy = dt.getUTCFullYear();
    if (isNaN(dt.getTime()) || yy < 0 || yy > 9999) return new Err("#NUM!");
    return Math.round((dt.getTime() - BASE_MS) / DAY_MS);
  };
  FNX.YEAR = function (a) { return dpart(a, function (dt) { return dt.getUTCFullYear(); }); };
  FNX.MONTH = function (a) { return dpart(a, function (dt) { return dt.getUTCMonth() + 1; }); };
  FNX.DAY = function (a) { return dpart(a, function (dt) { return dt.getUTCDate(); }); };
  FNX.WEEKDAY = function (a) {
    var ty = a.length > 1 ? dnum(a[1]) : 1;
    if (isErr(ty)) return ty;
    ty = Math.trunc(ty);
    var first = { 1: 0, 2: 1, 3: 1, 11: 1, 12: 2, 13: 3, 14: 4, 15: 5, 16: 6, 17: 0 };  // JS day number (Sunday 0) that starts the week
    var n = dnum(a[0]); if (isErr(n)) return n;
    if (!OWN.call(first, ty)) return new Err("#NUM!");
    var dt = serialDate(n); if (!dt) return new Err("#NUM!");
    var k = (dt.getUTCDay() - first[ty] + 7) % 7;
    return ty === 3 ? k : k + 1;
  };

  /* ---- math functions ----
   * The Google Docs Editors help pages for MOD, POWER, SQRT, FLOOR, CEILING and INT were read. Numbers: numeric text is
   * accepted as elsewhere in this engine; a range of more than one cell is #VALUE!. MOD takes the sign of the divisor
   * (MOD(-1,3) is 2), a zero divisor is #DIV/0!, and decimals can still show float noise, so ROUND the result as the page says.
   * POWER: 0 to a negative power is #DIV/0!, a negative base with a fractional exponent or an overflow is #NUM!;
   * 0 to the power 0 is 1 (not stated on the page, my choice). SQRT of a negative is #NUM!. INT rounds down, so INT(-1.5) is -2.
   * FLOOR and CEILING: factor defaults to 1; a zero factor is #DIV/0!. A negative factor is #NUM! in this engine: the pages
   * describe it in two ways, I did not verify the direction rules, so it is left out rather than guessed. FLOOR(v,f) is
   * the largest multiple of f at or below v, CEILING the smallest at or above; results are cleaned to 15 significant digits
   * so FLOOR(23.25,0.18) shows 23.22. The quotient is cleaned the same way before rounding, so MOD(0.3,0.1) is 0 and
   * CEILING(0.1+0.2,0.1) is 0.3 (plain floating point gives 0.1 and 0.4); this is my choice, the pages only warn about float noise. Not built: ROUNDUP, ROUNDDOWN, MROUND, EXP, LN, LOG. */
  function mn(v) { v = sc2(v); if (isErr(v)) return v; return num(v); }
  function clean(x) { return isFinite(x) ? Number(x.toPrecision(15)) : new Err("#NUM!"); }
  FNX.MOD = function (a) {
    var x = mn(a[0]), y = mn(a[1]); if (isErr(x)) return x; if (isErr(y)) return y;
    if (y === 0) return new Err("#DIV/0!");
    var q = clean(x / y); if (isErr(q)) return q;
    var r = x - y * Math.floor(q);
    return Math.abs(r) <= 1e-12 * Math.max(Math.abs(x), Math.abs(y)) ? 0 : clean(r);  // a remainder that is only float noise is 0
  };
  FNX.POWER = function (a) {
    var x = mn(a[0]), y = mn(a[1]); if (isErr(x)) return x; if (isErr(y)) return y;
    if (x === 0 && y < 0) return new Err("#DIV/0!");
    var r = Math.pow(x, y);
    return isFinite(r) ? r : new Err("#NUM!");
  };
  FNX.SQRT = function (a) { var x = mn(a[0]); if (isErr(x)) return x; return x < 0 ? new Err("#NUM!") : Math.sqrt(x); };
  FNX.INT = function (a) { var x = mn(a[0]); return isErr(x) ? x : Math.floor(x); };
  function stepFn(name, fn) {
    FNX[name] = function (a) {
      var x = mn(a[0]), f = a.length > 1 ? mn(a[1]) : 1; if (isErr(x)) return x; if (isErr(f)) return f;
      if (f === 0) return new Err("#DIV/0!");
      if (f < 0) return new Err("#NUM!");
      var q = clean(x / f); if (isErr(q)) return q;
      return clean(fn(q) * f);
    };
  }
  stepFn("FLOOR", Math.floor); stepFn("CEILING", Math.ceil);
  var ARITY = { MOD: [2, 2], POWER: [2, 2], SQRT: [1, 1], INT: [1, 1], FLOOR: [1, 2], CEILING: [1, 2], DATE: [3, 3], YEAR: [1, 1], MONTH: [1, 1], DAY: [1, 1], WEEKDAY: [1, 2], LEFT: [1, 2], RIGHT: [1, 2], MID: [3, 3], TRIM: [1, 1], SUBSTITUTE: [3, 4], FIND: [2, 3], INDEX: [2, 3], MATCH: [2, 3], VLOOKUP: [3, 4], SUMIF: [2, 3], COUNTIF: [2, 2], AVERAGEIF: [2, 3], SUM: [1, 99], AVERAGE: [1, 99], MIN: [1, 99], MAX: [1, 99], COUNT: [1, 99], ABS: [1, 1], ROUND: [1, 2], LEN: [1, 1], UPPER: [1, 1], LOWER: [1, 1], CONCAT: [1, 99], NOT: [1, 1], AND: [1, 99], OR: [1, 99], IF: [2, 3] };
  function truth(v) {
    if (isErr(v)) return v;
    if (typeof v === "boolean") return v;
    if (typeof v === "number") return v !== 0;
    if (v === null || v === "") return false;
    var s = String(v).toUpperCase();
    if (s === "TRUE") return true;
    if (s === "FALSE") return false;
    return new Err("#VALUE!");
  }
  function compare(o, l, r) {
    var ln = typeof l === "number" || typeof l === "boolean" || l === null, rn = typeof r === "number" || typeof r === "boolean" || r === null;
    var a, b;
    if (ln && rn) { a = num(l); b = num(r); }
    else if (ln !== rn && l !== "" && r !== "" && l !== null && r !== null) { a = ln ? 0 : 1; b = rn ? 0 : 1; }  // numbers sort before text
    else { a = text(l).toLowerCase(); b = text(r).toLowerCase(); }
    switch (o) { case "=": return a === b; case "<>": return a !== b; case "<": return a < b; case ">": return a > b; case "<=": return a <= b; default: return a >= b; }
  }

  /* cells: { "A1": "5" | "=A1+2" | "text" }. Returns a function get(addr) -> value (number, string, boolean, Err). */
  function Sheet(input) {
    var cells = Object.create(null), cache = Object.create(null), stack = Object.create(null), trees = Object.create(null), work = 0, depth = 0;
    if (input && typeof input === "object") Object.keys(input).forEach(function (k) {  // own keys only, valid addresses, plain bounded text
      var v = input[k];
      if (parseAddr(k) && (typeof v === "string" ? v.length <= MAX_CELL_TEXT : typeof v === "number" ? isFinite(v) : typeof v === "boolean")) cells[k] = v;
    });
    function raw(a) { if (typeof a !== "string" || !parseAddr(a)) return null; var v = cells[a]; return v === undefined ? null : v; }
    function literal(s) {
      if (typeof s !== "string") return s;
      if (s === "") return null;
      var n = /^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s.trim()) ? parseFloat(s) : NaN;
      if (isFinite(n)) return n;  // 1e999 stays text
      if (s.toUpperCase() === "TRUE") return true;
      if (s.toUpperCase() === "FALSE") return false;
      return s;
    }
    function get(a) {
      if (cache[a] !== undefined) return cache[a];
      var r = raw(a);
      if (typeof r !== "string" || r[0] !== "=") return (cache[a] = literal(r));
      if (stack[a]) return new Err("#CIRC!");      // not cached: every cell on the loop reports it
      if (depth >= MAX_DEPTH_EVAL) return new Err("#LIMIT!");
      stack[a] = true; depth++;
      var out;
      try {
        if (!trees[a]) trees[a] = parse(r.slice(1));
        out = ev(trees[a]);
      } catch (e) { out = e instanceof Err ? e : new Err("#VALUE!"); }
      delete stack[a]; depth--;
      return (cache[a] = out);
    }
    function cellValue(c) { var v = get(addr(c.col, c.row)); return v; }
    function ev(n) {
      switch (n.n) {
        case "lit": return n.v;
        case "ref_err": return new Err("#REF!");
        case "cell": return cellValue(n.a);
        case "range": {
          var c1 = Math.min(n.a.col, n.b.col), c2 = Math.max(n.a.col, n.b.col), r1 = Math.min(n.a.row, n.b.row), r2 = Math.max(n.a.row, n.b.row);
          if ((c2 - c1 + 1) * (r2 - r1 + 1) > MAX_RANGE) return new Err("#REF!");
          if ((work += (c2 - c1 + 1) * (r2 - r1 + 1)) > BUDGET) return new Err("#LIMIT!");
          var vals = [];
          for (var r = r1; r <= r2; r++) for (var c = c1; c <= c2; c++) vals.push(get(addr(c, r)));
          vals.rows = r2 - r1 + 1; vals.cols = c2 - c1 + 1;  // shape, for INDEX, MATCH and VLOOKUP
          return vals;
        }
        case "un": { var x = num(sc(ev(n.e))); if (isErr(x)) return x; return n.o === "-" ? -x : x; }
        case "bin": {
          var l = sc(ev(n.l)); if (isErr(l)) return l;
          var rr = sc(ev(n.r)); if (isErr(rr)) return rr;
          if (n.o === "&") { var tl = text(l), tr = text(rr); if (tl.length + tr.length > MAX_STR) return new Err("#VALUE!"); return tl + tr; }
          if (/^(=|<>|<=|>=|<|>)$/.test(n.o)) return compare(n.o, l, rr);
          var a = num(l), b = num(rr); if (isErr(a)) return a; if (isErr(b)) return b;
          var v;
          if (n.o === "+") v = a + b; else if (n.o === "-") v = a - b; else if (n.o === "*") v = a * b;
          else if (n.o === "/") { if (b === 0) return new Err("#DIV/0!"); v = a / b; } else v = Math.pow(a, b);
          return isFinite(v) ? v : new Err("#VALUE!");
        }
        case "call": {
          var ar = OWN.call(ARITY, n.f) ? ARITY[n.f] : null;
          if (!ar) return new Err("#NAME?");
          if (n.a.length < ar[0] || n.a.length > ar[1]) return new Err("#VALUE!");
          if (n.f === "IF") {  // only the chosen branch runs, so an unchosen error is not reported
            var cnd = truth(sc(ev(n.a[0]))); if (isErr(cnd)) return cnd;
            if (cnd) return sc(ev(n.a[1]));
            return n.a.length > 2 ? sc(ev(n.a[2])) : false;
          }
          var agg = !OWN.call(FNX, n.f) && (n.f === "SUM" || n.f === "AVERAGE" || n.f === "MIN" || n.f === "MAX" || n.f === "COUNT");
          // a direct cell reference counts as a reference (text and blanks ignored), like a one-cell range
          var args = n.a.map(function (x) { var v = ev(x); return agg && x.n === "cell" ? [v] : v; });
          var res;
          meter = function (k) { work += k; return work > BUDGET; };
          try { res = (OWN.call(FNX, n.f) ? FNX[n.f] : FN[n.f])(args); }
          catch (e) { if (e instanceof Err) return e; throw e; }
          finally { meter = null; }
          return typeof res === "number" && !isFinite(res) ? new Err("#VALUE!") : res;
        }
      }
    }
    function sc(v) { return Array.isArray(v) ? (v.length === 1 ? v[0] : new Err("#VALUE!")) : v; }
    // Dependencies are evaluated first in an explicit-stack post-order, so a long chain never
    // recurses deeply and the result does not depend on which cell is asked for first.
    function deps(a) {
      var r = raw(a), out = [];
      if (typeof r !== "string" || r[0] !== "=") return out;
      try { if (!trees[a]) trees[a] = parse(r.slice(1)); } catch (e) { return out; }
      (function walk(n) {
        if (!n) return;
        if (n.n === "cell") out.push(addr(n.a.col, n.a.row));
        else if (n.n === "range") {
          var c1 = Math.min(n.a.col, n.b.col), c2 = Math.max(n.a.col, n.b.col), r1 = Math.min(n.a.row, n.b.row), r2 = Math.max(n.a.row, n.b.row);
          if ((c2 - c1 + 1) * (r2 - r1 + 1) <= MAX_RANGE) for (var rr = r1; rr <= r2; rr++) for (var cc = c1; cc <= c2; cc++) out.push(addr(cc, rr));
        } else { walk(n.l); walk(n.r); walk(n.e); if (n.a && n.a.length) n.a.forEach(walk); }
      })(trees[a]);
      return out;
    }
    function resolve(a) {
      if (!parseAddr(a)) return new Err("#REF!");
      var path = Object.create(null), st = [[a, null, 0]];
      path[a] = true;
      while (st.length) {
        var f = st[st.length - 1];
        if (!f[1]) f[1] = deps(f[0]);
        if (f[2] < f[1].length) {
          var d = f[1][f[2]++];
          if (cache[d] === undefined && !path[d] && ++work <= BUDGET) { path[d] = true; st.push([d, null, 0]); }
        } else { st.pop(); delete path[f[0]]; get(f[0]); }
      }
      return get(a);
    }
    return { get: function (a) { var v = resolve(a); return v === null ? "" : v; }, raw: raw };
  }

  var api = { Sheet: Sheet, parse: parse, Err: Err, isErr: isErr, parseAddr: parseAddr, MAX_LEN: MAX_LEN };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowFormula = api;
})(typeof self !== "undefined" ? self : this);

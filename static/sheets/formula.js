/* Salow Sheets formula engine. Runs on the device (browser or node); it reads only the cells it is
 * given. No eval, no Function, no network. Formulas are parsed by a hand-written recursive-descent
 * parser into a tree, then walked. Errors are values: #DIV/0!, #VALUE!, #NAME?, #REF!, #CIRC!, #PARSE!. */
(function (root) {
  "use strict";
  var MAX_LEN = 2000, MAX_DEPTH = 50, MAX_RANGE = 10000, MAX_ROW = 1000, MAX_COL = 26;

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
      if ((m = /^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?|^\.[0-9]+/.exec(src.slice(i)))) { t.push({ k: "num", v: parseFloat(m[0]) }); i += m[0].length; continue; }
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
    if (/^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s)) return parseFloat(s);
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
  var FN = {
    SUM: function (a) { var n = flatten(a); return isErr(n) ? n : n.reduce(function (s, x) { return s + x; }, 0); },
    AVERAGE: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? n.reduce(function (s, x) { return s + x; }, 0) / n.length : new Err("#DIV/0!"); },
    MIN: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? Math.min.apply(null, n) : 0; },
    MAX: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? Math.max.apply(null, n) : 0; },
    COUNT: function (a) { var c = 0; a.forEach(function (x) { if (Array.isArray(x)) x.forEach(function (y) { if (typeof y === "number") c++; }); else if (!isErr(num(x))) c++; }); return c; },
    ABS: function (a) { var x = num(a[0]); return isErr(x) ? x : Math.abs(x); },
    ROUND: function (a) { var x = num(a[0]), d = a.length > 1 ? num(a[1]) : 0; if (isErr(x)) return x; if (isErr(d)) return d; var f = Math.pow(10, Math.trunc(d)); return Math.sign(x) * Math.round(Math.abs(x) * f + 1e-9) / f; },
    LEN: function (a) { var s = text(a[0]); return isErr(s) ? s : s.length; },
    UPPER: function (a) { var s = text(a[0]); return isErr(s) ? s : s.toUpperCase(); },
    LOWER: function (a) { var s = text(a[0]); return isErr(s) ? s : s.toLowerCase(); },
    CONCAT: function (a) { var o = ""; for (var i = 0; i < a.length; i++) { var s = text(a[i]); if (isErr(s)) return s; o += s; } return o; },
    NOT: function (a) { var x = truth(a[0]); return isErr(x) ? x : !x; },
    AND: function (a) { for (var i = 0; i < a.length; i++) { var x = truth(a[i]); if (isErr(x)) return x; if (!x) return false; } return true; },
    OR: function (a) { for (var i = 0; i < a.length; i++) { var x = truth(a[i]); if (isErr(x)) return x; if (x) return true; } return false; }
  };
  var ARITY = { SUM: [1, 99], AVERAGE: [1, 99], MIN: [1, 99], MAX: [1, 99], COUNT: [1, 99], ABS: [1, 1], ROUND: [1, 2], LEN: [1, 1], UPPER: [1, 1], LOWER: [1, 1], CONCAT: [1, 99], NOT: [1, 1], AND: [1, 99], OR: [1, 99], IF: [2, 3] };
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
  function Sheet(cells) {
    var cache = {}, stack = {}, trees = {};
    function raw(a) { var v = cells[a]; return v === undefined ? null : v; }
    function literal(s) {
      if (typeof s !== "string") return s;
      if (s === "") return null;
      var n = /^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s.trim()) ? parseFloat(s) : NaN;
      if (!isNaN(n)) return n;
      if (s.toUpperCase() === "TRUE") return true;
      if (s.toUpperCase() === "FALSE") return false;
      return s;
    }
    function get(a) {
      if (Object.prototype.hasOwnProperty.call(cache, a)) return cache[a];
      var r = raw(a);
      if (typeof r !== "string" || r[0] !== "=") return (cache[a] = literal(r));
      if (stack[a]) return new Err("#CIRC!");      // not cached: every cell on the loop reports it
      stack[a] = true;
      var out;
      try {
        if (!trees[a]) trees[a] = parse(r.slice(1));
        out = ev(trees[a]);
      } catch (e) { out = e instanceof Err ? e : new Err("#VALUE!"); }
      delete stack[a];
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
          var vals = [];
          for (var r = r1; r <= r2; r++) for (var c = c1; c <= c2; c++) vals.push(get(addr(c, r)));
          return vals;
        }
        case "un": { var x = num(sc(ev(n.e))); if (isErr(x)) return x; return n.o === "-" ? -x : x; }
        case "bin": {
          var l = sc(ev(n.l)); if (isErr(l)) return l;
          var rr = sc(ev(n.r)); if (isErr(rr)) return rr;
          if (n.o === "&") return text(l) + text(rr);
          if (/^(=|<>|<=|>=|<|>)$/.test(n.o)) return compare(n.o, l, rr);
          var a = num(l), b = num(rr); if (isErr(a)) return a; if (isErr(b)) return b;
          var v;
          if (n.o === "+") v = a + b; else if (n.o === "-") v = a - b; else if (n.o === "*") v = a * b;
          else if (n.o === "/") { if (b === 0) return new Err("#DIV/0!"); v = a / b; } else v = Math.pow(a, b);
          return isFinite(v) ? v : new Err("#VALUE!");
        }
        case "call": {
          var ar = ARITY[n.f];
          if (!ar) return new Err("#NAME?");
          if (n.a.length < ar[0] || n.a.length > ar[1]) return new Err("#VALUE!");
          if (n.f === "IF") {  // only the chosen branch runs, so an unchosen error is not reported
            var cnd = truth(sc(ev(n.a[0]))); if (isErr(cnd)) return cnd;
            if (cnd) return sc(ev(n.a[1]));
            return n.a.length > 2 ? sc(ev(n.a[2])) : false;
          }
          var args = n.a.map(ev);
          var res = FN[n.f](args);
          return typeof res === "number" && !isFinite(res) ? new Err("#VALUE!") : res;
        }
      }
    }
    function sc(v) { return Array.isArray(v) ? (v.length === 1 ? v[0] : new Err("#VALUE!")) : v; }
    return { get: function (a) { var v = get(a); return v === null ? "" : v; }, raw: raw };
  }

  var api = { Sheet: Sheet, parse: parse, Err: Err, isErr: isErr, parseAddr: parseAddr, MAX_LEN: MAX_LEN };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowFormula = api;
})(typeof self !== "undefined" ? self : this);

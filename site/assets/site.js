// Two small helpers; every page works without them.
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.add("js");
  var lang = (root.lang || "en").slice(0, 2);
  var words = {
    en: { menu: "Menu", copy: "Copy", copied: "Copied" },
    tr: { menu: "Menü", copy: "Kopyala", copied: "Kopyalandı" },
    zh: { menu: "菜单", copy: "复制", copied: "已复制" }
  }[lang] || { menu: "Menu", copy: "Copy", copied: "Copied" };

  // The menu button on narrow screens.
  var nav = document.querySelector(".site-nav");
  var btn = document.querySelector(".menu-btn");
  if (nav && btn) {
    btn.textContent = words.menu;
    btn.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // The colour theme: auto (the system's setting), light or dark. The choice is data-theme on <html>, which the
  // inline script in <head> sets again before the first paint of the next page.
  var themeBtn = document.querySelector(".theme-btn");
  if (themeBtn) {
    // "Theme|Auto|Light|Dark|follows your system|Change the colour theme", translated by the page
    var L = (themeBtn.getAttribute("data-labels") || "").split("|");
    var order = ["auto", "light", "dark"];
    var current = function () {
      var t = root.getAttribute("data-theme");
      return t === "light" || t === "dark" ? t : "auto";
    };
    var show = function () {
      var mode = current();
      var name = L[order.indexOf(mode) + 1];
      themeBtn.setAttribute("data-mode", mode);
      themeBtn.querySelector(".theme-name").textContent = name;
      themeBtn.setAttribute("aria-label", L[0] + ": " + name + (mode === "auto" ? " (" + L[4] + ")" : "") + ". " + L[5]);
      themeBtn.title = themeBtn.getAttribute("aria-label");
    };
    themeBtn.addEventListener("click", function () {
      var next = order[(order.indexOf(current()) + 1) % order.length];
      if (next === "auto") root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", next);
      try {
        if (next === "auto") localStorage.removeItem("mu300-theme");
        else localStorage.setItem("mu300-theme", next);
      } catch (e) { /* private window or blocked storage: the choice lasts for this page only */ }
      show();
    });
    show();
    themeBtn.hidden = false;
  }

  // A copy button on every command block. Lines starting with "#" and the text after " # " are comments
  // in the shell, so copying the whole block is always safe to paste.
  if (!navigator.clipboard) return;
  document.querySelectorAll("pre > code").forEach(function (code) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "copy";
    b.textContent = words.copy;
    b.addEventListener("click", function () {
      navigator.clipboard.writeText(code.innerText.replace(/\s+$/, "") + "\n").then(function () {
        b.textContent = words.copied;
        setTimeout(function () { b.textContent = words.copy; }, 1500);
      });
    });
    code.parentNode.appendChild(b);
  });
})();

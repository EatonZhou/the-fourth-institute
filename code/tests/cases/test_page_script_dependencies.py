"""Every global a page script uses comes from a script of the same page.

Portal pages share objects between classic scripts through ``window``
properties, so a page that leaves out a provider fails only when the user
reaches the code that uses it.  ``page_script_support`` reads which globals
each script provides and needs and which scripts each page loads, as the
server sends the page; a script that may run without a global tests for it.

:module: workbench.shell
:importance: P1
"""
from __future__ import annotations

from tests.cases import page_script_support as support


def test_page_scripts_find_the_globals_they_use():
    """每个页面脚本无条件使用的全局对象，都由同一页面的脚本、启动脚本或按需加载的脚本提供"""
    missing = support.missing_globals(support.scan_scripts(), support.pages())
    assert not missing, "\n".join(map(str, missing))


def test_pages_list_existing_scripts():
    """页面列出的每个 /static 脚本都是现有文件或服务端生成的路由脚本"""
    assert support.unknown_scripts(support.pages()) == []


def test_scan_reads_provided_needed_and_tested_globals():
    """扫描区分提供、无条件使用和先检测再用的全局对象，字符串、注释、正则和模板文字不算代码"""
    source = r'''(function (global) {
      "use strict";
      // window.InComment.use()
      const text = "window.InString.use()", pattern = /window\.InRegex/g;
      const label = `window.InTemplate ${global.Interpolated.name}`;
      global.Provided = Object.freeze({});
      window.Needed.call();
      if (window.Tested) window.Tested.call();
      window.Chained?.call();
      if (typeof window.Typed === "function") window.Typed();
    })(window);'''
    scanned = support.scan("example.js", source, set())
    assert scanned.provides == {"Provided"}
    assert scanned.needs == {"Interpolated": 5, "Needed": 7}


def test_pages_resolve_providers_and_on_demand_loads():
    """缺少提供者的页面被报告；页面列出提供者，或使用者按路径、按加载表加载提供者时不报告"""
    known = {"provider.js", "consumer.js", "loader.js", "table.js"}
    scanned = {
        "provider.js": support.scan("provider.js", "window.Shared = {};", known),
        "consumer.js": support.scan("consumer.js", "window.Shared.use();", known),
        "loader.js": support.scan("loader.js", 'load("/static/provider.js"); window.Shared.use();', known),
        "table.js": support.scan("table.js", 'const parts = [["provider", "Shared"]]; window.Shared.use();', known),
    }
    scanned["table.js"].loads |= {name for name, member in scanned["table.js"].loader_entries
                                  if member in scanned[name].provides}
    pages = {
        "without.html": [("file", "consumer.js")],
        "with.html": [("file", "provider.js"), ("file", "consumer.js")],
        "loaded.html": [("file", "loader.js")],
        "table.html": [("file", "table.js")],
        "inline.html": [("inline", "window.Shared = {};"), ("file", "consumer.js")],
    }
    missing = support.missing_globals(scanned, pages)
    assert [(item.page, item.script, item.member, item.providers) for item in missing] == [
        ("without.html", "consumer.js", "Shared", ("provider.js",))]

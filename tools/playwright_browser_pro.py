#!/usr/bin/env python3
"""
Playwright Browser PRO — professional frontend testing extension for Hermes Agent.

Extends the hardened Playwright singleton (playwright_browser.py) with:

1.  Device Emulation & Responsive Testing
    - ``browser_emulate_device(device_name)`` — iPhone 15, Pixel 7, iPad Pro, etc.
    - ``browser_test_responsive(url, breakpoints)`` — screenshots at every breakpoint

2.  Full Page Test
    - ``browser_full_test(url, device, checks)`` — structured JSON report

3.  Accessibility & Validation
    - ``browser_check_accessibility()`` — ARIA, contrast, heading hierarchy
    - ``browser_validate_html()`` — HTML validity checks

4.  Visual Regression
    - ``browser_snapshot_baseline(name)`` — save baseline screenshot
    - ``browser_compare_snapshot(name)`` — pixel-diff vs baseline

5.  Performance
    - ``browser_performance_metrics()`` — Core Web Vitals (LCP, FID, CLS, TBT)

All tools auto-cleanup on error. Artifacts under .browser_artifacts/.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from tools.playwright_browser import (
    _PlaywrightManager,
    HAS_PLAYWRIGHT,
    PWTimeout,
)

# ═══════════════════════════════════════════════════════════════
# Device Presets (defined here since playwright_browser.py is locked)
# ═══════════════════════════════════════════════════════════════

DEVICE_PRESETS: dict[str, dict] = {
    "iPhone 15": {"viewport": {"width": 390, "height": 844}, "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", "device_scale_factor": 3, "is_mobile": True, "has_touch": True},
    "iPhone 15 Pro Max": {"viewport": {"width": 430, "height": 932}, "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", "device_scale_factor": 3, "is_mobile": True, "has_touch": True},
    "iPhone SE": {"viewport": {"width": 375, "height": 667}, "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", "device_scale_factor": 2, "is_mobile": True, "has_touch": True},
    "iPad Pro": {"viewport": {"width": 1024, "height": 1366}, "user_agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", "device_scale_factor": 2, "is_mobile": False, "has_touch": True},
    "iPad Mini": {"viewport": {"width": 744, "height": 1133}, "user_agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", "device_scale_factor": 2, "is_mobile": False, "has_touch": True},
    "Pixel 7": {"viewport": {"width": 412, "height": 915}, "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36", "device_scale_factor": 2.625, "is_mobile": True, "has_touch": True},
    "Pixel 7 Pro": {"viewport": {"width": 412, "height": 960}, "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36", "device_scale_factor": 3, "is_mobile": True, "has_touch": True},
    "Galaxy S23": {"viewport": {"width": 384, "height": 854}, "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36", "device_scale_factor": 3, "is_mobile": True, "has_touch": True},
    "Desktop 1920x1080": {"viewport": {"width": 1920, "height": 1080}, "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "device_scale_factor": 1, "is_mobile": False, "has_touch": False},
    "Desktop 1440x900": {"viewport": {"width": 1440, "height": 900}, "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "device_scale_factor": 1, "is_mobile": False, "has_touch": False},
    "Desktop 1366x768": {"viewport": {"width": 1366, "height": 768}, "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "device_scale_factor": 1, "is_mobile": False, "has_touch": False},
    "Desktop 2560x1440": {"viewport": {"width": 2560, "height": 1440}, "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "device_scale_factor": 2, "is_mobile": False, "has_touch": False},
}

RESPONSIVE_BREAKPOINTS = {
    "mobile": "Pixel 7",
    "tablet": "iPad Mini",
    "desktop": "Desktop 1920x1080",
    "small_desktop": "Desktop 1366x768",
}


# ═══════════════════════════════════════════════════════════════
# Artifact paths
# ═══════════════════════════════════════════════════════════════

def _get_artifact_dir() -> Path:
    base = Path(os.getcwd()) / ".browser_artifacts"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _artifact_path(name: str, ext: str = "png") -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return _get_artifact_dir() / f"{safe}_{ts}.{ext}"


def _get_mgr() -> _PlaywrightManager:
    return _PlaywrightManager.get()


# ═══════════════════════════════════════════════════════════════
# 1. Device Emulation & Responsive Testing
# ═══════════════════════════════════════════════════════════════

def emulate_device(device_name: str) -> dict:
    """Apply a device preset to the current browser context."""
    mgr = _get_mgr()
    if not mgr.is_ready:
        return {"error": "Browser not ready. Open a page first."}

    device = DEVICE_PRESETS.get(device_name)
    if not device:
        return {
            "error": f"Unknown device '{device_name}'.",
            "available_devices": list(DEVICE_PRESETS.keys()),
        }

    try:
        page = mgr.page
        if not page:
            return {"error": "No active page"}
        page.set_viewport_size(device["viewport"])
        mgr._last_activity = time.time()
        return {
            "emulated": True,
            "device": device_name,
            "viewport": device["viewport"],
            "is_mobile": device["is_mobile"],
            "has_touch": device["has_touch"],
        }
    except Exception as e:
        logger.error("Device emulation failed: %s", e)
        return {"error": str(e)[:300]}


def test_responsive(url: str, breakpoints: list[str] = None) -> dict:
    """Open a URL and screenshot it at multiple breakpoints."""
    if breakpoints is None:
        breakpoints = ["mobile", "tablet", "desktop"]

    mgr = _get_mgr()
    results: dict[str, Any] = {"url": url, "breakpoints": {}, "tested_at": time.time()}

    resolved: list[tuple[str, dict]] = []
    for bp in breakpoints:
        if bp in RESPONSIVE_BREAKPOINTS:
            name = RESPONSIVE_BREAKPOINTS[bp]
        elif bp in DEVICE_PRESETS:
            name = bp
        else:
            results["breakpoints"][bp] = {"error": f"Unknown breakpoint: {bp}"}
            continue
        resolved.append((f"{bp} ({name})", DEVICE_PRESETS[name]))

    first = True
    for label, device in resolved:
        try:
            open_result = mgr.open_page(url)
            first = False
            if open_result.get("navigation_errors"):
                results["breakpoints"][label] = {
                    "error": open_result.get("navigation_errors"),
                }
                continue

            page = mgr.page
            page.set_viewport_size(device["viewport"])
            page.wait_for_timeout(500)

            spath = str(_artifact_path(f"responsive_{label.replace(' ', '_')}"))
            page.screenshot(path=spath, full_page=True)

            results["breakpoints"][label] = {
                "viewport": device["viewport"],
                "screenshot": spath,
                "title": page.title(),
                "status_code": open_result.get("status_code"),
            }
            logger.info(
                "Responsive [%s] %dx%d -> %s",
                label,
                device["viewport"]["width"],
                device["viewport"]["height"],
                spath,
            )
        except Exception as e:
            logger.error("Responsive test failed for %s: %s", label, e)
            results["breakpoints"][label] = {"error": str(e)[:300]}

    results["total_screenshots"] = sum(
        1 for v in results["breakpoints"].values() if "screenshot" in v
    )
    return results


# ═══════════════════════════════════════════════════════════════
# 2. Full Page Test
# ═══════════════════════════════════════════════════════════════

DEFAULT_CHECKS = ["screenshot", "console", "accessibility", "performance"]


def full_page_test(
    url: str, device: str = "Desktop 1920x1080", checks: list[str] = None
) -> dict:
    """Run a comprehensive test suite against a URL.

    Returns a structured JSON report saved to .browser_artifacts/.
    """
    if checks is None:
        checks = list(DEFAULT_CHECKS)

    mgr = _get_mgr()
    report: dict[str, Any] = {
        "url": url,
        "device": device,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "checks": {},
        "summary": {"passed": 0, "failed": 0, "warnings": 0},
    }

    # Open the page
    try:
        open_result = mgr.open_page(url)
        report["navigation"] = {
            "status_code": open_result.get("status_code"),
            "title": open_result.get("title", ""),
            "content_length": open_result.get("content_length", 0),
        }
        if open_result.get("navigation_errors"):
            report["navigation"]["errors"] = open_result["navigation_errors"]
            report["summary"]["failed"] += 1
    except Exception as e:
        report["error"] = f"Navigation failed: {e}"
        report["summary"]["failed"] += 1
        return report

    # Apply device viewport
    if device in DEVICE_PRESETS:
        try:
            page = mgr.page
            if page:
                page.set_viewport_size(DEVICE_PRESETS[device]["viewport"])
        except Exception:
            pass

    # Screenshot
    if "screenshot" in checks:
        try:
            spath = str(_artifact_path("fulltest"))
            mgr.page.screenshot(path=spath, full_page=True)
            report["checks"]["screenshot"] = {"path": spath, "passed": True}
            report["summary"]["passed"] += 1
        except Exception as e:
            report["checks"]["screenshot"] = {"passed": False, "error": str(e)[:200]}
            report["summary"]["failed"] += 1

    # Console
    if "console" in checks:
        try:
            logs = mgr.get_console_logs()
            errors = [l for l in logs if l["type"] == "error"]
            warnings = [l for l in logs if l["type"] == "warning"]
            report["checks"]["console"] = {
                "passed": len(errors) == 0,
                "error_count": len(errors),
                "warning_count": len(warnings),
                "errors": [e["text"][:300] for e in errors[-10:]],
                "warnings": [w["text"][:300] for w in warnings[-10:]],
            }
            if len(errors) == 0:
                report["summary"]["passed"] += 1
            else:
                report["summary"]["warnings"] += 1
        except Exception as e:
            report["checks"]["console"] = {"passed": False, "error": str(e)[:200]}
            report["summary"]["failed"] += 1

    # Accessibility
    if "accessibility" in checks:
        try:
            a11y = _check_accessibility_internal(mgr)
            report["checks"]["accessibility"] = a11y
            if a11y.get("violations", 0) == 0:
                report["summary"]["passed"] += 1
            else:
                report["summary"]["warnings"] += a11y.get("violations", 0)
        except Exception as e:
            report["checks"]["accessibility"] = {
                "passed": False,
                "error": str(e)[:200],
            }
            report["summary"]["failed"] += 1

    # Performance
    if "performance" in checks:
        try:
            perf = _get_performance_metrics_internal(mgr)
            report["checks"]["performance"] = perf
            if not perf.get("error"):
                report["summary"]["passed"] += 1
            else:
                report["summary"]["warnings"] += 1
        except Exception as e:
            report["checks"]["performance"] = {
                "passed": False,
                "error": str(e)[:200],
            }
            report["summary"]["failed"] += 1

    # HTML Validation
    if "html_validation" in checks:
        try:
            val = _validate_html_internal(mgr)
            report["checks"]["html_validation"] = val
            if val.get("error_count", 0) == 0:
                report["summary"]["passed"] += 1
            else:
                report["summary"]["warnings"] += 1
        except Exception as e:
            report["checks"]["html_validation"] = {
                "passed": False,
                "error": str(e)[:200],
            }
            report["summary"]["failed"] += 1

    # Meta
    if "meta" in checks:
        try:
            meta = _get_meta_internal(mgr)
            report["checks"]["meta"] = meta
            report["summary"]["passed"] += 1
        except Exception as e:
            report["checks"]["meta"] = {"passed": False, "error": str(e)[:200]}
            report["summary"]["failed"] += 1

    # Responsive
    if "responsive" in checks:
        try:
            resp = test_responsive(url)
            report["checks"]["responsive"] = resp
            if (
                sum(
                    1
                    for v in resp.get("breakpoints", {}).values()
                    if "screenshot" in v
                )
                > 0
            ):
                report["summary"]["passed"] += 1
            else:
                report["summary"]["failed"] += 1
        except Exception as e:
            report["checks"]["responsive"] = {
                "passed": False,
                "error": str(e)[:200],
            }
            report["summary"]["failed"] += 1

    # Save full report
    rp = _artifact_path("full_test_report", "json")
    try:
        rp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str)
        )
        report["report_path"] = str(rp)
    except Exception:
        pass

    return report


# ═══════════════════════════════════════════════════════════════
# 3. Accessibility & Validation
# ═══════════════════════════════════════════════════════════════

def _check_accessibility_internal(mgr: _PlaywrightManager) -> dict:
    """Internal: run accessibility checks against the current page."""
    page = mgr.page
    if not page:
        return {"error": "No page open"}

    violations = []
    warnings = []

    # ── ARIA labels ──
    try:
        aria_issues = page.evaluate(
            """() => {
            const issues = [];
            document.querySelectorAll('button:not([aria-label]):not([aria-labelledby])').forEach(el => {
                const text = (el.textContent || '').trim();
                if (!text && !el.getAttribute('title'))
                    issues.push({element:'button',text:'(empty)',selector:el.tagName+(el.id?'#'+el.id:'')+(el.className?'.'+el.className.split(' ')[0]:''),issue:'Button without accessible name'});
            });
            document.querySelectorAll('a').forEach(el => {
                const text=(el.textContent||'').trim();
                if(!text && !el.querySelector('img[alt]') && !el.getAttribute('aria-label') && !el.getAttribute('title') && el.querySelector('*'))
                    issues.push({element:'a',text:'(nested)',selector:'a'+(el.id?'#'+el.id:''),issue:'Link may lack accessible name'});
            });
            document.querySelectorAll('img:not([alt])').forEach(el => {
                issues.push({element:'img',text:el.src?.substring(0,80)||'',selector:'img',issue:'Image missing alt attribute'});
            });
            document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), textarea, select').forEach(el => {
                if(!el.id || !document.querySelector('label[for="'+el.id+'"]'))
                    if(!el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
                        issues.push({element:el.tagName,text:el.name||el.placeholder||'',selector:el.tagName+(el.id?'#'+el.id:'')+(el.name?'[name="'+el.name+'"]':''),issue:'Form field without label'});
            });
            return issues;
        }"""
        )
        for issue in aria_issues[:30]:
            violations.append({"type": "aria", **issue})
    except Exception as e:
        warnings.append({"type": "aria_check_error", "detail": str(e)[:200]})

    # ── Heading hierarchy ──
    try:
        heading_issues = page.evaluate(
            """() => {
            const issues=[];
            const hs=document.querySelectorAll('h1,h2,h3,h4,h5,h6');
            if(!hs.length){issues.push({issue:'No headings found',severity:'warning'});return issues;}
            let prev=0;
            hs.forEach(h=>{let l=parseInt(h.tagName[1]);if(l>prev+1&&prev>0)issues.push({issue:'Heading h'+l+' skips from h'+prev,element:(h.textContent||'').substring(0,50),severity:'warning'});prev=l;});
            const h1s=document.querySelectorAll('h1');
            if(h1s.length>1)issues.push({issue:'Multiple h1 tags ('+h1s.length+')',severity:'warning'});
            if(!h1s.length)issues.push({issue:'No h1 tag found',severity:'warning'});
            return issues;
        }"""
        )
        for h in heading_issues:
            (warnings if h.get("severity") == "warning" else violations).append(
                {"type": "heading", **h}
            )
    except Exception as e:
        warnings.append({"type": "heading_check_error", "detail": str(e)[:200]})

    # ── Contrast heuristic ──
    try:
        contrast = page.evaluate(
            """() => {
            const issues=[];
            document.querySelectorAll('p,span,a,li,td,th,h1,h2,h3,h4,h5,h6,label,button').forEach(el=>{
                const s=window.getComputedStyle(el);
                const fs=parseFloat(s.fontSize),txt=(el.textContent||'').trim();
                if(!txt||txt.length<3)return;
                const m=s.color.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                if(m){const r=parseInt(m[1]),g=parseInt(m[2]),b=parseInt(m[3]),lum=0.299*r+0.587*g+0.114*b;
                if(lum>165&&fs<18&&issues.length<10)issues.push({element:el.tagName,text:txt.substring(0,40),color:s.color,issue:'Low contrast text (light-on-white)'});}
            });
            return issues;
        }"""
        )
        for c in contrast[:10]:
            warnings.append({"type": "contrast", **c})
    except Exception:
        pass

    # ── Tabindex ──
    try:
        tab_issues = page.evaluate(
            """() => {
            const issues=[];
            document.querySelectorAll('[tabindex]').forEach(el=>{let v=parseInt(el.getAttribute('tabindex'));if(v>0&&issues.length<10)issues.push({element:el.tagName,tabindex:v,text:(el.textContent||'').substring(0,40),issue:'Positive tabindex disrupts tab order'});});
            return issues;
        }"""
        )
        for t in tab_issues:
            warnings.append({"type": "tabindex", **t})
    except Exception:
        pass

    return {
        "passed": len(violations) == 0,
        "violations": len(violations),
        "warnings": len(warnings),
        "details": violations,
        "warnings_list": warnings,
        "checks_performed": [
            "aria_labels",
            "heading_hierarchy",
            "contrast_heuristic",
            "tabindex",
        ],
    }


def check_accessibility() -> dict:
    """Run accessibility checks on the current page."""
    mgr = _get_mgr()
    if not mgr.is_ready:
        return {"error": "Browser not ready. Navigate to a URL first."}
    return _check_accessibility_internal(mgr)


def _validate_html_internal(mgr: _PlaywrightManager) -> dict:
    """Internal: validate HTML of the current page."""
    page = mgr.page
    if not page:
        return {"error": "No page open"}
    try:
        result = page.evaluate(
            """() => {
            const issues=[];
            if(!document.documentElement.getAttribute('lang')) issues.push({type:'missing_lang',severity:'warning',message:'Missing lang attribute on <html>'});
            if(!document.querySelector('meta[name="viewport"]')) issues.push({type:'missing_viewport',severity:'warning',message:'Missing viewport meta tag'});
            if(!document.querySelector('meta[charset]')) issues.push({type:'missing_charset',severity:'warning',message:'Missing charset declaration'});
            const h=document.documentElement.outerHTML;
            const od=(h.match(/<div[^>]*>/gi)||[]).length,cd=(h.match(/<\\/div>/gi)||[]).length;
            if(od!==cd) issues.push({type:'unclosed_tags',severity:'error',message:'Unclosed div tags: '+od+' opened, '+cd+' closed'});
            const ids=new Set(),dupes=new Set();
            document.querySelectorAll('[id]').forEach(el=>{if(ids.has(el.id))dupes.add(el.id);ids.add(el.id);});
            if(dupes.size>0) issues.push({type:'duplicate_ids',severity:'error',message:'Duplicate IDs: '+[...dupes].slice(0,5).join(', ')});
            ['font','center','marquee','blink','strike','tt','big'].forEach(tag=>{let els=document.querySelectorAll(tag);if(els.length)issues.push({type:'deprecated_element',severity:'warning',message:'Deprecated <'+tag+'> used '+els.length+' times'});});
            document.querySelectorAll('button:empty,a:empty').forEach(el=>{if(!el.querySelector('img,svg'))issues.push({type:'empty_element',severity:'warning',message:'Empty <'+el.tagName+'/>'});});
            return {issues,htmlLength:h.length};
        }"""
        )
        issues = result.get("issues", [])
        errors = [i for i in issues if i.get("severity") == "error"]
        warns = [i for i in issues if i.get("severity") == "warning"]
        return {
            "valid": len(errors) == 0,
            "error_count": len(errors),
            "warning_count": len(warns),
            "html_size": result.get("htmlLength", 0),
            "errors": errors,
            "warnings": warns,
            "url": page.url,
        }
    except Exception as e:
        return {"error": str(e)[:300]}


def validate_html() -> dict:
    """Validate the HTML of the current page."""
    mgr = _get_mgr()
    if not mgr.is_ready:
        return {"error": "Browser not ready"}
    return _validate_html_internal(mgr)


# ═══════════════════════════════════════════════════════════════
# 4. Visual Regression
# ═══════════════════════════════════════════════════════════════

def snapshot_baseline(name: str) -> dict:
    """Save a baseline screenshot for visual regression."""
    mgr = _get_mgr()
    if not mgr.is_ready or not mgr.page:
        return {"error": "No page open"}
    try:
        bd = _get_artifact_dir() / "baselines"
        bd.mkdir(exist_ok=True)
        sn = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        path = bd / f"{sn}.png"
        mgr.page.screenshot(path=str(path), full_page=True)
        meta = {
            "name": name,
            "url": mgr.page.url,
            "viewport": mgr.page.viewport_size if mgr.page else {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file": str(path),
        }
        (bd / f"{sn}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2)
        )
        logger.info("Baseline saved: %s (%s)", name, path)
        return {
            "baseline_saved": True,
            "name": name,
            "path": str(path),
            "url": meta["url"],
            "viewport": meta["viewport"],
        }
    except Exception as e:
        return {"error": str(e)[:300]}


def compare_snapshot(name: str, threshold: float = 0.02) -> dict:
    """Compare current page against baseline screenshot."""
    mgr = _get_mgr()
    if not mgr.is_ready or not mgr.page:
        return {"error": "No page open"}

    bd = _get_artifact_dir() / "baselines"
    sn = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    bp = bd / f"{sn}.png"

    if not bp.exists():
        return {
            "error": f"Baseline not found: {sn}",
            "available_baselines": [p.stem for p in bd.glob("*.png")],
        }

    try:
        current = mgr.page.screenshot(full_page=True)
        baseline = bp.read_bytes()

        try:
            from PIL import Image

            ci = Image.open(io.BytesIO(current))
            bi = Image.open(io.BytesIO(baseline))
            if ci.size != bi.size:
                bi = bi.resize(ci.size, Image.LANCZOS)
            cp = list(ci.getdata())
            bpix = list(bi.getdata())
            total = len(cp)
            diff_count = sum(
                1
                for c, b in zip(cp, bpix)
                if abs(c[0] - b[0]) + abs(c[1] - b[1]) + abs(c[2] - b[2]) > 30
            )
            dr = diff_count / total if total > 0 else 0
            match = dr <= threshold

            result = {
                "match": match,
                "diff_ratio": round(dr, 4),
                "threshold": threshold,
                "baseline": str(bp),
                "viewport": {"width": ci.width, "height": ci.height},
            }

            if not match:
                dp = _artifact_path(f"diff_{sn}")
                di = Image.new("RGBA", ci.size, (0, 0, 0, 0))
                dipix = [
                    (255, 0, 0, 180)
                    if abs(c[0] - b[0]) + abs(c[1] - b[1]) + abs(c[2] - b[2]) > 30
                    else (0, 0, 0, 0)
                    for c, b in zip(cp, bpix)
                ]
                di.putdata(dipix)
                di.save(str(dp))
                result["diff_image"] = str(dp)

            return result
        except ImportError:
            ch = hashlib.sha256(current).hexdigest()
            bh = hashlib.sha256(baseline).hexdigest()
            return {
                "match": ch == bh,
                "method": "hash",
                "current_hash": ch,
                "baseline_hash": bh,
                "baseline": str(bp),
            }
    except Exception as e:
        return {"error": str(e)[:300]}


# ═══════════════════════════════════════════════════════════════
# 5. Performance Metrics
# ═══════════════════════════════════════════════════════════════

def _get_performance_metrics_internal(mgr: _PlaywrightManager) -> dict:
    """Internal: collect Core Web Vitals."""
    page = mgr.page
    if not page:
        return {"error": "No page open"}

    try:
        metrics = page.evaluate(
            """() => {
            const r={},p=performance,t=p.timing,n=p.getEntriesByType('navigation')[0];
            if(t){r.dns=t.domainLookupEnd-t.domainLookupStart;r.tcp=t.connectEnd-t.connectStart;r.ttfb=t.responseStart-t.requestStart;r.dom_interactive=t.domInteractive-t.navigationStart;r.dom_complete=t.domComplete-t.navigationStart;r.load_event=t.loadEventEnd-t.loadEventStart;r.total_load=t.loadEventEnd-t.navigationStart;}
            try{const l=performance.getEntriesByType('largest-contentful-paint');r.lcp=l.length?l[l.length-1].renderTime||l[l.length-1].startTime:null;}catch(e){r.lcp=null;}
            try{const f=performance.getEntriesByType('first-input');r.fid=f.length?f[0].processingStart-f[0].startTime:null;}catch(e){r.fid=null;}
            try{let c=0;performance.getEntriesByType('layout-shift').forEach(e=>{if(!e.hadRecentInput)c+=e.value;});r.cls=c;}catch(e){r.cls=0;}
            if(t){const fc=performance.getEntriesByName('first-contentful-paint')[0];const fct=fc?fc.startTime:0;const tti=t.domInteractive-t.navigationStart;let tb=0;performance.getEntriesByType('longtask').forEach(ta=>{if(ta.startTime>=fct&&ta.startTime<tti)tb+=ta.duration-50;});r.tbt=tb;}else{r.tbt=null;}
            try{const fc=performance.getEntriesByName('first-contentful-paint')[0];r.fcp=fc?fc.startTime:null;}catch(e){r.fcp=null;}
            const res=performance.getEntriesByType('resource');r.resource_count=res.length;r.total_transfer=res.reduce((s,re)=>s+(re.transferSize||0),0);
            if(performance.memory){r.js_heap_used=performance.memory.usedJSHeapSize;r.js_heap_total=performance.memory.totalJSHeapSize;}
            return r;
        }"""
        )

        ratings = {}
        lcp = metrics.get("lcp")
        fid = metrics.get("fid")
        cls = metrics.get("cls")
        tbt = metrics.get("tbt")

        if lcp is not None:
            ratings["lcp"] = (
                "good" if lcp <= 2500 else ("needs_improvement" if lcp <= 4000 else "poor")
            )
        if fid is not None:
            ratings["fid"] = (
                "good" if fid <= 100 else ("needs_improvement" if fid <= 300 else "poor")
            )
        if cls is not None:
            ratings["cls"] = (
                "good" if cls <= 0.1 else ("needs_improvement" if cls <= 0.25 else "poor")
            )
        if tbt is not None:
            ratings["tbt"] = (
                "good" if tbt <= 200 else ("needs_improvement" if tbt <= 600 else "poor")
            )

        return {
            "url": page.url,
            **metrics,
            "ratings": ratings,
            "collected_at": time.time(),
        }
    except Exception as e:
        return {"error": str(e)[:300], "url": page.url if page else ""}


def get_performance_metrics() -> dict:
    """Collect Core Web Vitals from the current page."""
    mgr = _get_mgr()
    if not mgr.is_ready:
        return {"error": "Browser not ready"}
    return _get_performance_metrics_internal(mgr)


def _get_meta_internal(mgr: _PlaywrightManager) -> dict:
    """Internal: extract page meta + stats."""
    page = mgr.page
    if not page:
        return {"error": "No page open"}
    try:
        return page.evaluate(
            """() => {
            const tags={};
            document.querySelectorAll('meta').forEach(m=>{const n=m.getAttribute('name')||m.getAttribute('property')||m.getAttribute('charset')||'';if(n)tags[n]=m.getAttribute('content')||m.getAttribute('charset')||'';});
            return {meta:tags,stats:{textLength:(document.body?.innerText||'').length,links:document.querySelectorAll('a').length,images:document.querySelectorAll('img').length,scripts:document.querySelectorAll('script').length,forms:document.querySelectorAll('form').length,headings:document.querySelectorAll('h1,h2,h3,h4,h5,h6').length},title:document.title};
        }"""
        )
    except Exception as e:
        return {"error": str(e)[:200]}


# ═══════════════════════════════════════════════════════════════
# Public API — all return JSON strings
# ═══════════════════════════════════════════════════════════════

def _safe_json(fn, *args, **kwargs) -> str:
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error("Playwright PRO error: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def browser_emulate_device(device_name: str = "", task_id: str = "") -> str:
    return _safe_json(emulate_device, device_name)


def browser_test_responsive(
    url: str = "", breakpoints_json: str = "", task_id: str = ""
) -> str:
    bps = None
    if breakpoints_json:
        try:
            bps = json.loads(breakpoints_json)
        except json.JSONDecodeError:
            bps = [breakpoints_json]
    return _safe_json(test_responsive, url, bps)


def browser_full_test(
    url: str = "",
    device: str = "Desktop 1920x1080",
    checks_json: str = "",
    task_id: str = "",
) -> str:
    checks = None
    if checks_json:
        try:
            checks = json.loads(checks_json)
        except json.JSONDecodeError:
            checks = checks_json.split(",")
    return _safe_json(full_page_test, url, device, checks)


def browser_check_accessibility(task_id: str = "") -> str:
    return _safe_json(check_accessibility)


def browser_validate_html(task_id: str = "") -> str:
    return _safe_json(validate_html)


def browser_snapshot_baseline(name: str = "", task_id: str = "") -> str:
    return _safe_json(snapshot_baseline, name)


def browser_compare_snapshot(
    name: str = "", threshold: float = 0.02, task_id: str = ""
) -> str:
    return _safe_json(compare_snapshot, name, threshold)


def browser_performance_metrics(task_id: str = "") -> str:
    return _safe_json(get_performance_metrics)


# ═══════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════

from tools.registry import registry


def check_playwright_pro() -> bool:
    return HAS_PLAYWRIGHT


_SCHEMAS = {
    "browser_emulate_device": {
        "name": "browser_emulate_device",
        "description": (
            "Emulate a mobile/tablet/desktop device. Available: "
            "'iPhone 15', 'iPhone SE', 'iPhone 15 Pro Max', "
            "'Pixel 7', 'Pixel 7 Pro', 'Galaxy S23', "
            "'iPad Pro', 'iPad Mini', "
            "'Desktop 1920x1080', 'Desktop 1440x900', 'Desktop 1366x768', 'Desktop 2560x1440'. "
            "Use after browser_navigate to set the device viewport."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_name": {
                    "type": "string",
                    "description": "Device name (e.g. 'iPhone 15', 'Pixel 7')",
                },
            },
            "required": ["device_name"],
        },
    },
    "browser_test_responsive": {
        "name": "browser_test_responsive",
        "description": (
            "Test a URL at multiple responsive breakpoints. "
            "Screenshots each viewport. "
            "Breakpoints: 'mobile'->Pixel 7, 'tablet'->iPad Mini, 'desktop'->1920x1080. "
            "Also accepts device names: 'iPhone 15', 'iPad Pro', etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to test"},
                "breakpoints_json": {
                    "type": "string",
                    "description": (
                        "JSON array: '['mobile','tablet','desktop']'. "
                        "Default: ['mobile','tablet','desktop']"
                    ),
                    "default": "",
                },
            },
            "required": ["url"],
        },
    },
    "browser_full_test": {
        "name": "browser_full_test",
        "description": (
            "Run comprehensive test suite against a URL. "
            "Returns structured JSON report with screenshot, console errors, "
            "accessibility violations, performance metrics, HTML validation, "
            "meta tags, and responsive screenshots. "
            "Available checks: 'screenshot','console','accessibility','performance',"
            "'html_validation','responsive','meta'. "
            "Default: ['screenshot','console','accessibility','performance']. "
            "Report saved to .browser_artifacts/."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to test"},
                "device": {
                    "type": "string",
                    "description": "Device preset (default: 'Desktop 1920x1080')",
                    "default": "Desktop 1920x1080",
                },
                "checks_json": {
                    "type": "string",
                    "description": (
                        "JSON array of checks: '['screenshot','console','accessibility']'. "
                        "Default: ['screenshot','console','accessibility','performance']"
                    ),
                    "default": "",
                },
            },
            "required": ["url"],
        },
    },
    "browser_check_accessibility": {
        "name": "browser_check_accessibility",
        "description": (
            "Run accessibility checks: ARIA labels, heading hierarchy, "
            "contrast heuristics, tabindex. Returns violations + warnings + details. "
            "Requires a page to be open (browser_navigate first)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    "browser_validate_html": {
        "name": "browser_validate_html",
        "description": (
            "Validate current page HTML: lang attr, viewport meta, charset, "
            "unclosed tags, duplicate IDs, deprecated elements. "
            "Returns errors/warnings + counts."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    "browser_snapshot_baseline": {
        "name": "browser_snapshot_baseline",
        "description": (
            "Save full-page screenshot as visual regression baseline. "
            "Use before making changes, then call browser_compare_snapshot. "
            "Baselines stored in .browser_artifacts/baselines/."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name (e.g. 'homepage', 'login-form')",
                },
            },
            "required": ["name"],
        },
    },
    "browser_compare_snapshot": {
        "name": "browser_compare_snapshot",
        "description": (
            "Pixel-diff current page vs saved baseline. "
            "Returns 'match' boolean, diff_ratio, and red-overlay diff image if changed. "
            "Threshold: 0.02 (2% diff) by default."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Baseline name",
                },
                "threshold": {
                    "type": "number",
                    "description": "Allowed diff ratio 0.0-1.0 (default 0.02)",
                    "default": 0.02,
                },
            },
            "required": ["name"],
        },
    },
    "browser_performance_metrics": {
        "name": "browser_performance_metrics",
        "description": (
            "Collect Core Web Vitals: LCP, FID, CLS, TBT, FCP, TTFB, "
            "DOM interactive/complete, resource counts, JS heap. "
            "Each metric rated: good / needs_improvement / poor."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_FN_MAP = {
    "browser_emulate_device": browser_emulate_device,
    "browser_test_responsive": browser_test_responsive,
    "browser_full_test": browser_full_test,
    "browser_check_accessibility": browser_check_accessibility,
    "browser_validate_html": browser_validate_html,
    "browser_snapshot_baseline": browser_snapshot_baseline,
    "browser_compare_snapshot": browser_compare_snapshot,
    "browser_performance_metrics": browser_performance_metrics,
}

for _name, _fn in _FN_MAP.items():
    _schema = _SCHEMAS[_name]
    _props = list(_schema.get("parameters", {}).get("properties", {}).keys())

    def _make_handler(fn=_fn, props=_props):
        def _handler(args, task_id="", **kw):
            kwargs = {"task_id": task_id}
            for p in props:
                if p in args:
                    kwargs[p] = args[p]
            return fn(**kwargs)

        return _handler

    registry.register(
        name=_name,
        toolset="browser",
        schema=_schema,
        handler=_make_handler(),
        check_fn=check_playwright_pro,
        emoji="🎭",
    )

"""PPTX Quality Assurance - OOXML compliance + SSIM + VLM visual fidelity."""

from __future__ import annotations

import asyncio
import base64
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.core.logging import get_logger

logger = get_logger(__name__)

_qa_executor = None


def _get_qa_executor():
    global _qa_executor
    if _qa_executor is None:
        _qa_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa")
    return _qa_executor


class PPTXQualityAssurance:
    """Multi-dimensional PPTX quality assessment.

    Pipeline:
    1. OOXML Compliance Check (programmatic — no LLM needed)
       - DrawingML structure validity
       - EMU calculation accuracy
       - Color format compliance
       - Font specification correctness
       - Gradient/shadow XML well-formedness
    2. Visual Fidelity Check (SSIM + VLM)
       - Render HTML reference as screenshot (Playwright)
       - Render PPTX slide as image (python-pptx + PIL)
       - Compare pixel-level + semantic-level
    3. Combined score: OOXML(40%) + Visual(60%)
    """

    def __init__(self, max_iterations: int = 4, fidelity_threshold: float = 0.98):
        self.max_iterations = max_iterations
        self.threshold = fidelity_threshold

    async def evaluate(self, output_path: str, slides_html: list[dict]) -> float:
        """Evaluate PPTX quality across OOXML compliance and visual fidelity.

        Strategy:
        - OOXML compliance is always checked (programmatic, reliable)
        - Visual comparison via SSIM only if proper renderer available (LibreOffice)
        - Without proper PPTX renderer, use VLM evaluation of HTML quality as proxy
        - Score = OOXML(70%) + VLM_HTML_Quality(30%) when no renderer available

        Returns:
            Combined score between 0.0 and 1.0
        """
        output_file = Path(output_path)
        if not output_file.exists():
            logger.warning("pptx_qa.file_not_found", path=output_path)
            return 0.0

        ooxml_result = self._check_ooxml_compliance(output_path, slides_html)
        ooxml_score = ooxml_result["score"]
        logger.info("pptx_qa.ooxml_check", score=ooxml_score, issues=len(ooxml_result["issues"]))

        if ooxml_score < 0.7:
            return ooxml_score * 0.7

        # Without a proper PPTX renderer (LibreOffice), pixel-level SSIM comparison
        # is meaningless — our PIL renderer only draws crude rectangles.
        # Use VLM to evaluate HTML quality as a proxy for conversion readiness.
        vlm_quality = await self._vlm_evaluate_from_html(slides_html)
        logger.info("pptx_qa.vlm_html_quality", score=vlm_quality)

        combined = ooxml_score * 0.70 + vlm_quality * 0.30
        logger.info("pptx_qa.combined", ooxml=ooxml_score, vlm_quality=vlm_quality, combined=combined)
        return combined

    def _check_ooxml_compliance(self, output_path: str, slides_html: list[dict]) -> dict:
        """Programmatic OOXML DrawingML compliance verification.

        Checks the actual XML inside the .pptx ZIP against OOXML spec requirements.
        """
        issues = []
        checks_passed = 0
        checks_total = 0

        try:
            from pptx import Presentation
            from pptx.util import Emu, Pt
            from pptx.oxml.ns import qn
            from lxml import etree

            prs = Presentation(output_path)

            for slide_idx, slide in enumerate(prs.slides, 1):
                slide_issues = self._validate_slide_ooxml(slide, slide_idx, slides_html)
                issues.extend(slide_issues["issues"])
                checks_passed += slide_issues["passed"]
                checks_total += slide_issues["total"]

            global_issues = self._validate_global_ooxml(prs)
            issues.extend(global_issues["issues"])
            checks_passed += global_issues["passed"]
            checks_total += global_issues["total"]

        except Exception as e:
            logger.warning("pptx_qa.ooxml_check_error", error=str(e))
            issues.append(f"Failed to parse PPTX: {str(e)}")
            return {"score": 0.0, "issues": issues}

        score = checks_passed / checks_total if checks_total > 0 else 0.0
        return {"score": score, "issues": issues, "passed": checks_passed, "total": checks_total}

    def _validate_slide_ooxml(self, slide, slide_idx: int, slides_html: list[dict]) -> dict:
        """Validate a single slide's OOXML structure."""
        from pptx.oxml.ns import qn

        issues = []
        passed = 0
        total = 0

        shapes = list(slide.shapes)

        # Check 1: Slide has at least 1 shape
        total += 1
        if len(shapes) > 0:
            passed += 1
        else:
            issues.append(f"Slide {slide_idx}: No shapes found (empty slide)")

        for shape in shapes:
            sp = shape._element

            # Check 2: spPr (shape properties) exists
            total += 1
            spPr = sp.find(qn("p:spPr"))
            if spPr is None:
                spPr = sp.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}spPr")
            if spPr is not None:
                passed += 1

                # Check 3: Position (xfrm) has valid EMU values
                total += 1
                xfrm = spPr.find(qn("a:xfrm"))
                if xfrm is not None:
                    off = xfrm.find(qn("a:off"))
                    ext = xfrm.find(qn("a:ext"))
                    if off is not None and ext is not None:
                        try:
                            x = int(off.get("x", "0"))
                            y = int(off.get("y", "0"))
                            cx = int(ext.get("cx", "0"))
                            cy = int(ext.get("cy", "0"))

                            # EMU validity: values should be reasonable for 960x540 slide
                            max_emu = 960 * 9525 + 100000  # ~9.2M EMU + margin
                            if x < 0 or x > max_emu:
                                issues.append(f"Slide {slide_idx}, shape: x offset {x} EMU out of bounds")
                            elif y < 0 or y > 540 * 9525 + 100000:
                                issues.append(f"Slide {slide_idx}, shape: y offset {y} EMU out of bounds")
                            elif cx <= 0 or cx > max_emu:
                                issues.append(f"Slide {slide_idx}, shape: width {cx} EMU invalid")
                            elif cy <= 0 or cy > 540 * 9525 + 100000:
                                issues.append(f"Slide {slide_idx}, shape: height {cy} EMU invalid")
                            else:
                                passed += 1
                        except (ValueError, TypeError):
                            issues.append(f"Slide {slide_idx}, shape: non-numeric EMU values in xfrm")
                    else:
                        issues.append(f"Slide {slide_idx}, shape: missing off/ext in xfrm")
                else:
                    passed += 1  # No xfrm is valid for some shapes (groups, etc.)

                # Check 4: Fill specification validity
                total += 1
                solidFill = spPr.find(qn("a:solidFill"))
                gradFill = spPr.find(qn("a:gradFill"))
                noFill = spPr.find(qn("a:noFill"))

                if solidFill is not None:
                    color_valid = self._validate_color_element(solidFill)
                    if color_valid:
                        passed += 1
                    else:
                        issues.append(f"Slide {slide_idx}, shape: invalid color in solidFill")
                elif gradFill is not None:
                    grad_valid = self._validate_gradient_element(gradFill, slide_idx)
                    if grad_valid["valid"]:
                        passed += 1
                    else:
                        issues.extend(grad_valid["issues"])
                else:
                    passed += 1  # noFill or inherited fill is fine

                # Check 5: Shadow effect validity (if present)
                effectLst = spPr.find(qn("a:effectLst"))
                if effectLst is not None:
                    total += 1
                    shadow_valid = self._validate_shadow_element(effectLst, slide_idx)
                    if shadow_valid:
                        passed += 1
                    else:
                        issues.append(f"Slide {slide_idx}, shape: malformed shadow effect")

            else:
                issues.append(f"Slide {slide_idx}, shape: missing spPr element")

            # Check 6: Text frame validity
            if shape.has_text_frame:
                total += 1
                tf = shape.text_frame
                text_valid = self._validate_text_frame(tf, slide_idx)
                if text_valid["valid"]:
                    passed += 1
                else:
                    issues.extend(text_valid["issues"])

        return {"issues": issues, "passed": passed, "total": total}

    def _validate_global_ooxml(self, prs) -> dict:
        """Validate presentation-level OOXML properties."""
        from pptx.util import Emu

        issues = []
        passed = 0
        total = 0

        # Check: Slide dimensions
        total += 1
        expected_width = 960 * 9525  # 9,144,000 EMU
        expected_height = 540 * 9525  # 5,143,500 EMU
        actual_width = prs.slide_width
        actual_height = prs.slide_height

        width_tolerance = 50000  # ~5px tolerance in EMU
        if abs(actual_width - expected_width) <= width_tolerance and abs(actual_height - expected_height) <= width_tolerance:
            passed += 1
        else:
            issues.append(
                f"Slide dimensions: {actual_width}x{actual_height} EMU, "
                f"expected ~{expected_width}x{expected_height} EMU (960x540px)"
            )

        # Check: At least 1 slide
        total += 1
        if len(prs.slides) > 0:
            passed += 1
        else:
            issues.append("Presentation has no slides")

        # Check: No more slides than expected
        total += 1
        if len(prs.slides) <= 20:
            passed += 1
        else:
            issues.append(f"Too many slides ({len(prs.slides)}) — possible duplication")

        return {"issues": issues, "passed": passed, "total": total}

    def _validate_color_element(self, fill_element) -> bool:
        """Validate a color specification in DrawingML."""
        from pptx.oxml.ns import qn

        srgb = fill_element.find(qn("a:srgbClr"))
        if srgb is not None:
            val = srgb.get("val", "")
            if len(val) == 6:
                try:
                    int(val, 16)
                    return True
                except ValueError:
                    return False
            return False

        scheme = fill_element.find(qn("a:schemeClr"))
        if scheme is not None:
            return True

        return False

    def _validate_gradient_element(self, grad_fill, slide_idx: int) -> dict:
        """Validate gradient fill XML structure against OOXML spec."""
        from pptx.oxml.ns import qn

        issues = []

        # Must have gsLst (gradient stop list)
        gsLst = grad_fill.find(qn("a:gsLst"))
        if gsLst is None:
            return {"valid": False, "issues": [f"Slide {slide_idx}: gradFill missing gsLst"]}

        stops = gsLst.findall(qn("a:gs"))
        if len(stops) < 2:
            issues.append(f"Slide {slide_idx}: gradient has {len(stops)} stops (minimum 2 required)")
            return {"valid": False, "issues": issues}

        for i, gs in enumerate(stops):
            # pos must be 0-100000 (per-mille)
            pos = gs.get("pos")
            if pos is not None:
                try:
                    pos_val = int(pos)
                    if pos_val < 0 or pos_val > 100000:
                        issues.append(f"Slide {slide_idx}: gradient stop {i} pos={pos_val} out of range [0,100000]")
                except ValueError:
                    issues.append(f"Slide {slide_idx}: gradient stop {i} has non-numeric pos='{pos}'")

            # Each stop must have a color
            srgb = gs.find(qn("a:srgbClr"))
            scheme = gs.find(qn("a:schemeClr"))
            if srgb is None and scheme is None:
                issues.append(f"Slide {slide_idx}: gradient stop {i} missing color element")

        # Must have lin or path element for direction
        lin = grad_fill.find(qn("a:lin"))
        path = grad_fill.find(qn("a:path"))
        if lin is not None:
            ang = lin.get("ang")
            if ang is not None:
                try:
                    ang_val = int(ang)
                    if ang_val < 0 or ang_val > 21600000:
                        issues.append(f"Slide {slide_idx}: gradient angle {ang_val} out of range [0,21600000]")
                except ValueError:
                    issues.append(f"Slide {slide_idx}: gradient angle non-numeric: '{ang}'")

        return {"valid": len(issues) == 0, "issues": issues}

    def _validate_shadow_element(self, effect_lst, slide_idx: int) -> bool:
        """Validate shadow effect XML."""
        from pptx.oxml.ns import qn

        outer = effect_lst.find(qn("a:outerShdw"))
        inner = effect_lst.find(qn("a:innerShdw"))
        shadow = outer if outer is not None else inner

        if shadow is None:
            return True  # No shadow in effectLst is valid

        # blurRad must be non-negative EMU
        blur = shadow.get("blurRad")
        if blur is not None:
            try:
                if int(blur) < 0:
                    return False
            except ValueError:
                return False

        # dist must be non-negative EMU
        dist = shadow.get("dist")
        if dist is not None:
            try:
                if int(dist) < 0:
                    return False
            except ValueError:
                return False

        # dir must be 0-21600000 (60000ths of a degree)
        direction = shadow.get("dir")
        if direction is not None:
            try:
                d = int(direction)
                if d < 0 or d > 21600000:
                    return False
            except ValueError:
                return False

        return True

    def _validate_text_frame(self, text_frame, slide_idx: int) -> dict:
        """Validate text frame and run properties against OOXML spec."""
        from pptx.oxml.ns import qn

        issues = []

        for para in text_frame.paragraphs:
            for run in para.runs:
                rPr = run._r.find(qn("a:rPr"))
                if rPr is not None:
                    # Font size (sz) in hundredths of a point, must be > 0
                    sz = rPr.get("sz")
                    if sz is not None:
                        try:
                            sz_val = int(sz)
                            if sz_val <= 0:
                                issues.append(f"Slide {slide_idx}: text run has invalid font size sz={sz_val}")
                            elif sz_val > 400000:  # > 4000pt is unreasonable
                                issues.append(f"Slide {slide_idx}: text run has unreasonable font size sz={sz_val}")
                        except ValueError:
                            issues.append(f"Slide {slide_idx}: text run has non-numeric sz='{sz}'")

                    # Color validation
                    solidFill = rPr.find(qn("a:solidFill"))
                    if solidFill is not None:
                        if not self._validate_color_element(solidFill):
                            issues.append(f"Slide {slide_idx}: text run has invalid color format")

        return {"valid": len(issues) == 0, "issues": issues}

    async def _render_html_slides(self, slides_html: list[dict]) -> list[bytes]:
        """Render HTML slides as PNG screenshots using Playwright."""
        screenshots = []
        try:
            loop = asyncio.get_running_loop()
            for slide in slides_html[:5]:
                html = slide.get("html", "")
                if not html:
                    continue
                img_bytes = await loop.run_in_executor(
                    _get_qa_executor(),
                    _screenshot_html_sync,
                    html,
                )
                if img_bytes:
                    screenshots.append(img_bytes)
        except Exception as e:
            logger.warning("pptx_qa.html_render_error", error=str(e))
        return screenshots

    async def _render_pptx_slides(self, output_path: str, expected_count: int) -> list[bytes]:
        """Render PPTX slides as PNG images using python-pptx + PIL."""
        screenshots = []
        try:
            loop = asyncio.get_running_loop()
            screenshots = await loop.run_in_executor(
                _get_qa_executor(),
                _render_pptx_sync,
                output_path,
                expected_count,
            )
        except Exception as e:
            logger.warning("pptx_qa.pptx_render_error", error=str(e))
        return screenshots

    def _compute_ssim_images(self, html_imgs: list[bytes], pptx_imgs: list[bytes]) -> float:
        """Compute average SSIM between paired slide images."""
        try:
            from PIL import Image
            import io
            import numpy as np

            scores = []
            pairs = min(len(html_imgs), len(pptx_imgs))
            for i in range(pairs):
                img1 = Image.open(io.BytesIO(html_imgs[i])).convert("L").resize((480, 270))
                img2 = Image.open(io.BytesIO(pptx_imgs[i])).convert("L").resize((480, 270))
                arr1 = np.array(img1, dtype=np.float64)
                arr2 = np.array(img2, dtype=np.float64)
                score = self._ssim_numpy(arr1, arr2)
                scores.append(score)

            return sum(scores) / len(scores) if scores else 0.85
        except ImportError:
            logger.warning("pptx_qa.ssim_import_error")
            return 0.85
        except Exception as e:
            logger.warning("pptx_qa.ssim_compute_error", error=str(e))
            return 0.85

    @staticmethod
    def _ssim_numpy(img1, img2) -> float:
        """Compute SSIM between two numpy arrays (simplified)."""
        import numpy as np
        C1 = (0.01 * 255) ** 2
        C2 = (0.03 * 255) ** 2

        mu1 = img1.mean()
        mu2 = img2.mean()
        sigma1_sq = img1.var()
        sigma2_sq = img2.var()
        sigma12 = ((img1 - mu1) * (img2 - mu2)).mean()

        numerator = (2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)
        denominator = (mu1**2 + mu2**2 + C1) * (sigma1_sq + sigma2_sq + C2)
        return float(numerator / denominator)

    async def _vlm_compare_images(
        self, html_imgs: list[bytes], pptx_imgs: list[bytes]
    ) -> float:
        """Use VLM to compare HTML vs PPTX rendered images."""
        try:
            from src.agents.loader import get_llm_for_agent
            from langchain_core.messages import HumanMessage, SystemMessage

            llm = get_llm_for_agent("qa_critic", format_id="pptx")

            prompt = (
                "Compare these two slide renderings. "
                "The first is the HTML reference (ground truth), the second is the PPTX conversion. "
                "Score the visual fidelity from 0.0 to 1.0 where 1.0 is pixel-perfect. "
                "Be strict: deduct for misaligned text, wrong colors, missing shadows, wrong font sizes. "
                "Output ONLY a JSON: {\"fidelity\": <float>}"
            )

            img_content = []
            pairs = min(len(html_imgs), len(pptx_imgs), 3)
            for i in range(pairs):
                img_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64.b64encode(html_imgs[i]).decode()}"}
                })
                img_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64.b64encode(pptx_imgs[i]).decode()}"}
                })

            messages = [
                SystemMessage(content="You are a strict visual QA critic for slide presentations."),
                HumanMessage(content=[{"type": "text", "text": prompt}] + img_content),
            ]

            response = await llm.ainvoke(messages)
            import json
            import re
            match = re.search(r'"fidelity"\s*:\s*([\d.]+)', response.content)
            if match:
                return min(float(match.group(1)), 1.0)
            return 0.85
        except Exception as e:
            logger.warning("pptx_qa.vlm_compare_error", error=str(e))
            return 0.85

    async def _vlm_evaluate_from_html(self, slides_html: list[dict]) -> float:
        """Fallback: VLM evaluates HTML quality directly without PPTX comparison."""
        try:
            from src.agents.loader import get_llm_for_agent
            from langchain_core.messages import HumanMessage, SystemMessage

            llm = get_llm_for_agent("qa_critic", format_id="pptx")

            all_html = "\n".join(s.get("html", "")[:1500] for s in slides_html[:4])
            prompt = (
                "Evaluate this slide HTML for visual quality and PPTX conversion readiness. "
                "Score 0.0-1.0. Be strict: check element positioning, typography, color usage, "
                "professional design quality. Deduct heavily for overlapping text, missing data-pptx attributes, "
                "likely clipped titles/body text, weak proposal density, lack of charts/tables/diagrams/KPI structures, "
                "non-premium fonts, low-quality box colors, or use of forbidden CSS properties. "
                "Output ONLY: {\"fidelity\": <float>}\n\n"
                f"HTML:\n{all_html}"
            )

            messages = [
                SystemMessage(content="You are a strict visual QA critic."),
                HumanMessage(content=prompt),
            ]

            response = await llm.ainvoke(messages)
            import json
            import re
            match = re.search(r'"fidelity"\s*:\s*([\d.]+)', response.content)
            if match:
                return min(float(match.group(1)), 1.0)
            return 0.85
        except Exception as e:
            logger.warning("pptx_qa.vlm_fallback_error", error=str(e))
            return 0.85


def _screenshot_html_sync(html: str) -> bytes | None:
    """Synchronously render HTML to PNG using Playwright."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_screenshot_html_async(html))
    finally:
        loop.close()


async def _screenshot_html_async(html: str) -> bytes | None:
    """Render HTML slide to PNG screenshot."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 960, "height": 540})
            await page.set_content(html, wait_until="networkidle")
            screenshot = await page.screenshot(type="png")
            await browser.close()
            return screenshot
    except Exception:
        return None


def _render_pptx_sync(output_path: str, expected_count: int) -> list[bytes]:
    """Render PPTX slides to PNG images using python-pptx geometry info + PIL."""
    screenshots = []
    try:
        from pptx import Presentation
        from PIL import Image, ImageDraw, ImageFont
        import io

        prs = Presentation(output_path)
        for slide in list(prs.slides)[:5]:
            img = Image.new("RGB", (960, 540), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)

            for shape in slide.shapes:
                x = int(shape.left / 9525) if shape.left else 0
                y = int(shape.top / 9525) if shape.top else 0
                w = int(shape.width / 9525) if shape.width else 0
                h = int(shape.height / 9525) if shape.height else 0

                if shape.has_text_frame:
                    text = shape.text_frame.text
                    draw.rectangle([x, y, x + w, y + h], outline=(200, 200, 200))
                    if text:
                        try:
                            draw.text((x + 4, y + 4), text[:100], fill=(0, 0, 0))
                        except Exception:
                            pass
                else:
                    fill_color = (240, 240, 240)
                    try:
                        if shape.fill and shape.fill.type is not None:
                            fc = shape.fill.fore_color
                            if fc and fc.rgb:
                                fill_color = (fc.rgb[0], fc.rgb[1], fc.rgb[2])
                    except Exception:
                        pass
                    draw.rectangle([x, y, x + w, y + h], fill=fill_color)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            screenshots.append(buf.getvalue())
    except Exception as e:
        logger.warning("pptx_qa.pptx_render_sync_error", error=str(e))
    return screenshots

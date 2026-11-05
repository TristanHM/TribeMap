"""Browser test of the plan editor: drives Microsoft Edge (already on Windows) through Playwright.

Skipped when Playwright or Edge isn't available. Uses the made-up sample data, never the real DB.
"""
import socket
import threading
import time

import pytest

from conftest import SAMPLE
from tribemap import db, importer, plans

playwright = pytest.importorskip("playwright.sync_api")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cfg, terrain, sample_file):
    import uvicorn

    from tribemap.web import create_app
    conn = db.connect(cfg.db_path)
    importer.import_report(conn, cfg, terrain, sample_file)
    plans.add(conn, "906-06", "0999", "move", "N")
    conn.close()
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


@pytest.fixture
def page(server):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception as e:  # no Edge on this machine
            pytest.skip(f"Edge not available: {e}")
        page = browser.new_page(viewport={"width": 1500, "height": 950}, accept_downloads=True)
        page.set_default_timeout(8000)   # the page answers in well under a second; don't sit 30 s on a stuck one
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.errors = errors   # a test expecting an error (e.g. a refused file) removes it
        page.goto(server + "/?plan=906-06")
        try:
            page.wait_for_selector('polygon[data-id="QB 1016"]')
        except playwright.TimeoutError:
            browser.close()
            # a script error stops the map drawing: say so rather than just "timed out"
            pytest.fail(f"the map didn't draw; page errors: {errors or 'none'}")
        yield page
        browser.close()
        assert not errors, errors


def orders(page):
    return page.input_value("#eOrders")


def test_draw_save_edit_delete_a_scout_route(page):
    assert page.is_hidden("#newPlan")
    page.click("#newScout")
    page.wait_for_selector("#editForm", state="visible")
    page.wait_for_function("document.querySelectorAll('#gEdit circle').length >= 6")   # next-step costs

    # the scout leaves from the move's end (QB 1015); clicking QB 1013 adds a straight line N,N
    page.click('polygon[data-id="QB 1013"]')
    page.wait_for_function("document.querySelector('#eOrders').value === 'N,N'")
    assert "0999 scout 1" in page.inner_text("#eStatus")
    assert page.locator("#eSteps tr").count() == 3   # header + 2 steps

    page.click('.pad button[data-dir="NE"]')
    page.wait_for_function("document.querySelector('#eOrders').value === 'N,N,NE'")
    page.click("#eUndo")
    page.wait_for_function("document.querySelector('#eOrders').value === 'N,N'")

    page.click("#editForm button[type=submit]")
    page.wait_for_selector("#editForm", state="hidden")
    page.wait_for_selector('tr.route[data-route="0999 scout 1"]')

    page.click('button[data-edit="0999 scout 1"]')
    page.wait_for_selector("#eDelete", state="visible")
    assert orders(page) == "N,N"
    page.click("#eDelete")
    assert "again" in page.inner_text("#eDelete")
    page.click("#eDelete")
    page.wait_for_selector("#editForm", state="hidden")
    assert page.locator('tr.route[data-route="0999 scout 1"]').count() == 0


def test_usually_mounted(page):
    page.click('button[data-edit="0999 move"]')
    page.wait_for_selector("#eUsual", state="visible")
    assert not page.is_checked("#eUsual") and "foot (unit's usual)" in page.inner_text("#eMover")
    page.check("#eUsual")
    page.wait_for_selector("#planMsg:has-text('0999 usually moves mounted')")
    page.wait_for_function("document.querySelector('#eStatus').innerText.includes('27')")
    assert "mounted (unit's usual)" in page.inner_text("#eMover")
    page.uncheck("#eUsual")
    page.wait_for_selector("#planMsg:has-text('0999 moves on foot')")


def test_new_plan_and_picked_start(page):
    page.select_option("#plan", "__new")
    page.fill("#newPlanTurn", "906-07")
    page.click("#newPlan button[type=submit]")
    page.wait_for_selector("#editTurn:has-text('906-07')")
    page.click("#newMove")
    page.select_option("#eUnitPick", "__other")
    page.fill("#eUnit", "1999")
    page.click("#ePick")
    page.click('polygon[data-id="QB 1016"]')
    page.wait_for_function("document.querySelector('#eStart').value === 'QB 1016'")
    page.select_option("#eMover", "mounted")
    page.click('.pad button[data-dir="NE"]')
    page.wait_for_function("document.querySelector('#eStatus').innerText.includes('27')")
    page.click("#editForm button[type=submit]")
    page.wait_for_selector('tr.route[data-route="1999 move"]')


def test_one_tribe_selected_at_a_time(page):
    # a second tribe with a scout party, so there is something to switch between
    page.evaluate("""async () => {
        const post = (draft) => fetch('/api/plan/906-06/route', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({draft})});
        await post({unit: '0999', kind: 'scout', orders: ['S']});
        await post({unit: '1999', kind: 'scout', orders: ['N'], start: 'QB 1016'});
    }""")
    page.evaluate("loadAll(true)")
    page.wait_for_selector('tr.route[data-route="1999 scout 1"]')
    assert page.is_visible("#unitPicker")
    # exactly one unit selected; the other tribe is faded
    assert page.locator("#unitPicker .pick.on").count() == 1
    page.click('#unitPicker button[data-select="0999"]')
    page.wait_for_selector('tr.route.faded[data-route="1999 scout 1"]')
    assert page.locator('tr.route.faded[data-route="0999 scout 1"]').count() == 0
    page.click('#unitPicker button[data-select="1999"]')
    page.wait_for_selector('tr.route.faded[data-route="0999 scout 1"]')
    assert page.locator('tr.route.faded[data-route="1999 scout 1"]').count() == 0
    # editing a faded tribe's route selects that tribe
    page.click('button[data-edit="0999 scout 1"]')
    page.wait_for_selector('#unitPicker button.on[data-select="0999"]')


def test_auto_plan_button(page):
    page.click("#autoScout")
    page.wait_for_selector("#planMsg:has-text('Auto-planned')")
    page.wait_for_function("document.querySelectorAll('tr.route').length === 9")   # the move + 8 parties


def test_auto_plan_per_tribe_and_element(page):
    # an element moving too: Auto on each move row plans that unit, sharing the tribe's 8 parties
    page.evaluate("""async () => {
        await fetch('/api/plan/906-06/route', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({draft: {unit: '0999e1', kind: 'move', orders: ['S'], start: 'QB 1016'}})});
    }""")
    page.evaluate("loadAll(true)")
    page.click('button[data-auto="0999e1"]')
    page.wait_for_selector("#planMsg:has-text('Auto-planned 4 scout parties for 0999e1')")
    assert page.inner_text("#autoScout") == "Auto-plan 0999e1 scouts"      # it selected the element
    page.wait_for_selector('tr.route[data-route="0999e1 scout 4"]')
    assert page.locator('tr.route[data-route^="0999 scout"]').count() == 0
    page.click('button[data-auto="0999"]')
    page.wait_for_selector("#planMsg:has-text('for 0999:')")
    page.wait_for_function("document.querySelectorAll('tr.route[data-route*=\" scout \"]').length === 8")


def test_clicking_a_table_row_selects_its_tribe(page):
    page.evaluate("""async () => {
        await fetch('/api/plan/906-06/route', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({draft: {unit: '1999', kind: 'scout', orders: ['N'], start: 'QB 1016'}})});
    }""")
    page.evaluate("loadAll(true)")
    page.wait_for_selector('tr.route[data-route="1999 scout 1"]')
    page.click('tr.route[data-route="1999 scout 1"] td:nth-child(2)')
    page.wait_for_selector('#unitPicker button.on[data-select="1999"]')
    page.click('tr.route[data-route="0999 move"] td:nth-child(2)')
    page.wait_for_selector('#unitPicker button.on[data-select="0999"]')


def test_new_plan_is_a_variant_of_the_next_turn(page):
    # the sample's latest report is 906-05, so plans are for 906-06; its main plan exists (a move N)
    page.select_option("#plan", "__new")
    assert page.input_value("#newPlanTurn") == "906-06" and page.input_value("#newPlanName") == "B"
    assert page.is_checked("#newPlanCopy")
    page.click("#newPlan button[type=submit]")
    page.wait_for_selector("#planMsg:has-text('a copy of 906-06')")
    page.wait_for_selector("#planTitle:has-text('906-06 · B')")
    page.wait_for_selector('tr.route[data-route="0999 move"]')        # the copied move
    # the same name again is refused, and a bad turn is caught
    page.select_option("#plan", "__new")
    page.fill("#newPlanName", "B")
    page.click("#newPlan button[type=submit]")
    page.wait_for_selector("#newPlanErr:has-text('already exists')")
    page.fill("#newPlanTurn", "906-13")
    page.click("#newPlan button[type=submit]")
    assert "month" in page.inner_text("#newPlanErr") and page.is_visible("#newPlan")
    # delete the variant (two clicks)
    page.click("#newPlanCancel")
    page.click("#deletePlan")
    page.click("#deletePlan")
    page.wait_for_selector("#planMsg:has-text('Deleted plan 906-06 · B')")


def test_delete_button_on_rows(page):
    page.evaluate("""async () => {
        await fetch('/api/plan/906-06/route', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({draft: {unit: '0999', kind: 'scout', orders: ['S']}})});
    }""")
    page.evaluate("loadAll(true)")
    page.wait_for_selector('button[data-del="0999 scout 1"]')
    page.click('button[data-del="0999 scout 1"]')
    assert page.inner_text('button[data-del="0999 scout 1"]') == "Delete?"
    page.click('button[data-del="0999 scout 1"]')
    page.wait_for_selector("#planMsg:has-text('Deleted 0999 scout 1')")
    page.wait_for_selector('tr.route[data-route="0999 scout 1"]', state="detached")
    # the MP column says how much is used, with no "out of MP" notes cluttering the table
    assert "out of MP" not in page.inner_text("#planSummary")


def test_delete_all_scouting_in_a_group(page):
    page.evaluate("""async () => {
        for (const orders of [['S'], ['N']]) {
            await fetch('/api/plan/906-06/route', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({draft: {unit: '0999', kind: 'scout', orders}})});
        }
    }""")
    page.evaluate("loadAll(true)")
    page.wait_for_selector('button[data-delgroup="0999"]')
    page.click('button[data-delgroup="0999"]')
    assert page.inner_text('button[data-delgroup="0999"]') == "Delete all?"
    page.click('button[data-delgroup="0999"]')
    page.wait_for_selector("#planMsg:has-text(\"Deleted 2 of 0999's scout routes\")")
    page.wait_for_function("!document.querySelector('tr.route[data-route*=\" scout \"]')")
    assert page.query_selector('tr.route[data-route="0999 move"]')   # the unit's move stays


def test_clicking_the_route_shortens_it(page):
    page.click("#newScout")
    page.wait_for_selector("#editForm", state="visible")
    # the scout leaves from the move's end, QB 1015
    page.click('polygon[data-id="QB 1012"]')
    page.wait_for_function("document.querySelector('#eOrders').value === 'N,N,N'")
    page.click('polygon[data-id="QB 1012"]')     # its last hex: that step goes
    page.wait_for_function("document.querySelector('#eOrders').value === 'N,N'")
    page.click('polygon[data-id="QB 1014"]')     # an earlier hex: cut back to there
    page.wait_for_function("document.querySelector('#eOrders').value === 'N'")
    page.click('polygon[data-id="QB 1013"]')     # off the route: extends again
    page.wait_for_function("document.querySelector('#eOrders').value === 'N,N'")
    page.click('polygon[data-id="QB 1015"]')     # the start: clears it
    page.wait_for_function("document.querySelector('#eOrders').value === ''")


def test_old_map_style(page):
    assert not page.is_checked('input[data-layer="old"]')          # off unless asked for
    assert page.is_hidden("#compass")
    page.check('input[data-layer="old"]')
    page.wait_for_selector("#map.old")
    assert page.is_visible("#compass") and page.is_visible("#cartoucheBox") and page.is_hidden("#gUnits")
    assert page.locator('#gText use[href^="#ink-"]').count() > 0    # the known land is drawn in ink symbols
    assert page.locator("#gText .lbl").count() == 0                 # no hex labels or codes
    box = page.locator('polygon[data-id]').first.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert page.is_hidden("#tip")                                    # no hover details on the picture
    page.uncheck('input[data-layer="old"]')
    page.wait_for_selector("#map:not(.old)")
    assert page.locator('#gText use[href^="#ink-"]').count() == 0


def test_layers_left_out_of_the_browser_version(page):
    # the browser-only version holds just the player's own reports: these layers aren't offered
    layers = page.eval_on_selector_all("#layers input", "is => is.map(i => i.dataset.layer)")
    assert "terrain" in layers and not {"others", "people"} & set(layers) and "old" in layers
    assert "Unverified" not in page.inner_text("#legend")


def test_save_the_map_as_shown(page):
    with page.expect_download(timeout=60000) as dl:
        page.click("#zsave")
    d = dl.value
    assert d.suggested_filename.startswith("tribemap-") and d.suggested_filename.endswith(".png")
    assert open(d.path(), "rb").read(4) == bytes([0x89]) + b"PNG"   # a PNG file
    assert page.inner_text("#zsave") == "Save"


def test_bulk_import_is_automatic_and_in_turn_order(page):
    # given newest first, plus a file that isn't a report: imported at once, oldest turn first, no confirm step
    t7 = SAMPLE.replace("Current Turn 906-05 (#77)", "Current Turn 906-07 (#79)").replace(
        "Next Turn 906-06 (#78)", "Next Turn 906-08 (#80)")
    t6 = SAMPLE.replace("Current Turn 906-05 (#77)", "Current Turn 906-06 (#78)").replace(
        "Next Turn 906-06 (#78)", "Next Turn 906-07 (#79)")
    page.set_input_files("#importFile", [
        {"name": "0999_906_07_Results.txt", "mimeType": "text/plain", "buffer": t7.encode()},
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"},
        {"name": "0999_906_06_Results.txt", "mimeType": "text/plain", "buffer": t6.encode()},
    ])
    page.wait_for_selector("#importDone", state="visible")
    items = page.locator("ul.imports > li").all_inner_texts()
    assert len(items) == 3
    assert "notes.txt" in items[0] and "turn report" in items[0]
    assert items[1].startswith("0999_906_06_Results.txt: turn 906-06")
    assert items[2].startswith("0999_906_07_Results.txt: turn 906-07") and "plan 906-08 ready" in items[2]
    page.wait_for_function("[...document.querySelectorAll('#asof option')].some((o) => o.value === '906-07')")
    page.errors.remove("Failed to load resource: the server responded with a status of 400 (Bad Request)")


def test_history_range(page, sample_file):
    assert page.is_hidden("#historyBox")   # one turn: nothing to look back over
    newer = sample_file.with_name("0999_906_06_Results.txt")
    newer.write_text(SAMPLE.replace("906-06 (#78), 18/11", "906-07 (#79), 18/12").replace("906-05 (#77)", "906-06 (#78)")
                     .replace("QB 1016, (Previous Hex = QB 1017)", "QB 1015, (Previous Hex = QB 1016)"), encoding="utf-8")
    page.set_input_files("#importFile", str(newer))
    page.wait_for_selector("#importDone", state="visible")
    page.click("#importDone")
    page.wait_for_selector("#historyBox", state="visible")
    assert page.inner_text("#histLabel") == "906-05 to 906-06 (2 turns)"
    dots = lambda: page.eval_on_selector_all("#gHistory circle title", "ts => ts.map(t => t.textContent)")
    assert dots() == []
    assert not page.is_checked("#histOn")   # off each time the page opens
    page.check("#histOn")
    page.wait_for_function("document.querySelectorAll('#gHistory circle').length > 0")
    assert "0999 906-05: QB 1016" in dots() and "0999 906-06: QB 1015" in dots()
    # dragging the first handle past the second pushes it along: just the last turn
    page.fill("#histFrom", "1")
    page.wait_for_selector("#histLabel:has-text('Turn 906-06')")
    page.wait_for_function("![...document.querySelectorAll('#gHistory circle title')].some(t => t.textContent.includes('906-05'))")
    assert "0999 906-06: QB 1015" in dots() and page.input_value("#histTo") == "1"
    page.reload()
    page.wait_for_selector("#historyBox", state="visible")
    assert not page.is_checked("#histOn") and page.inner_text("#histLabel") == "Turn 906-06"   # the range is kept


def test_import_a_whole_folder(page, tmp_path):
    # the reports anywhere inside it, oldest first; orders, news and Word's lock files are left alone
    t6 = SAMPLE.replace("Current Turn 906-05 (#77)", "Current Turn 906-06 (#78)").replace(
        "Next Turn 906-06 (#78)", "Next Turn 906-07 (#79)")
    top = tmp_path / "TribeNet"
    (top / "906-7" / "old").mkdir(parents=True)
    (top / "906-7" / "0999_906_06_Results.txt").write_text(t6, encoding="utf-8")
    (top / "906-7" / "old" / "0999_906_05_Results.txt").write_text(SAMPLE, encoding="utf-8")
    (top / "906-7" / "06 906 Tribe News.txt").write_text("news", encoding="utf-8")
    (top / "906-7" / "~$999_906_06_Results.txt").write_text("lock", encoding="utf-8")
    (top / "0999_906_7_Orders.xlsx").write_bytes(b"PK")
    page.set_input_files("#importDir", str(top))
    page.wait_for_selector("#importDone", state="visible")
    items = page.locator("ul.imports > li").all_inner_texts()
    assert len(items) == 2
    assert items[0] == "0999_906_05_Results.txt: already imported; nothing changed"   # the fixture imported it
    assert items[1].startswith("0999_906_06_Results.txt: turn 906-06")


def test_a_new_route_is_for_the_selected_unit(page):
    page.evaluate("""() => fetch('/api/plan/906-06/route', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({draft: {unit: '1999', kind: 'scout', orders: ['N'], start: 'QB 1016'}})})""")
    page.evaluate("loadAll(true)")
    page.click('#unitPicker button[data-select="1999"]')
    page.click("#newMove")
    page.wait_for_selector("#editForm", state="visible")
    # every unit is offered (not just those matching what's filled in), the selected one first choice
    assert page.input_value("#eUnitPick") == "1999" and page.input_value("#eUnit") == "1999"
    assert page.eval_on_selector_all("#eUnitPick option", "os => os.map(o => o.value)") == ["0999", "1999", "__other"]
    page.select_option("#eUnitPick", "0999")
    assert page.input_value("#eUnit") == "0999" and page.is_hidden("#eUnit")


def test_go_to_a_hex_or_unit(page):
    page.fill("#gotoText", "qb1016")
    page.press("#gotoText", "Enter")
    page.wait_for_selector("#detail:has-text('QB 1016')")
    page.fill("#gotoText", "0999")
    page.press("#gotoText", "Enter")
    page.wait_for_selector("#detail:has-text('QB 1016')")
    assert "?" not in page.url.split("/")[-1].replace("?plan=906-06", "")   # the page wasn't reloaded by the form


def test_layers_and_legend_fold_away(page):
    assert page.is_visible("#layers") and page.is_visible("#legend")
    page.click("#layersFold summary")
    page.click("#legendFold summary")
    assert page.is_hidden("#layers") and page.is_hidden("#legend")
    page.reload()
    page.wait_for_selector('polygon[data-id="QB 1016"]')
    assert page.is_hidden("#layers") and page.is_hidden("#legend")   # remembered
    page.click("#layersFold summary")
    assert page.is_visible("#layers")
    assert page.locator("details#historyFold.fold").count() == 1   # History folds the same way

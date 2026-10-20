import { expect, test } from "@playwright/test";
import assert from "node:assert/strict";

// All writes target uniquely named fixtures in an explicitly opted-in disposable installation.
// No mocked API responses or configured provider dispatches are used.
test.describe("real business workspace acceptance", () => {
  test.setTimeout(120_000);
  test.skip(
    process.env.BLUEPRINT_E2E_ALLOW_WRITES !== "1" ||
      !process.env.BLUEPRINT_E2E_EMAIL ||
      !process.env.BLUEPRINT_E2E_PASSWORD,
    "Requires a disposable workspace, explicit write opt-in and dedicated E2E credentials. Protected governance fixtures remain for policy enforcement.",
  );
  test("database, task, workday, calendar and anonymous form persistence", async ({
    browser,
    baseURL,
  }, testInfo) => {
    const ctx = await browser.newContext({
      baseURL,
      viewport: { width: 1512, height: 982 },
    });
    const page = await ctx.newPage();
    page.setDefaultTimeout(10000);
    const prefix = "BP-BIZ-E2E-" + Date.now().toString(36);
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const creds = {
      email: process.env.BLUEPRINT_E2E_EMAIL,
      password: process.env.BLUEPRINT_E2E_PASSWORD,
    };
    const api = async (path, body, method = body ? "POST" : "GET") =>
      page.evaluate(
        async ({ path, body, method }) => {
          const r = await fetch("/api/v1" + path, {
            method,
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/json",
              "X-Blueprint-Request": "1",
            },
            ...(body ? { body: JSON.stringify(body) } : {}),
          });
          const data = await r.json();
          if (!r.ok) throw new Error(r.status + " " + JSON.stringify(data));
          return data;
        },
        { path, body, method },
      );
    const nav = async (section) => {
      await page.goto(baseURL + "/#" + section);
      await page
        .locator(
          "main .business-page,main .workflow-editor,main .note-editor,main .canvas-editor",
        )
        .first()
        .waitFor();
    };
    const modal = () => page.getByRole("dialog");
    const save = async (label) => {
      await modal().getByRole("button", { name: label, exact: true }).click();
      await modal().waitFor({ state: "hidden" });
    };
    try {
      await page.goto(baseURL + "/");
      await page.getByLabel("電郵地址").waitFor();
      if (await page.getByLabel("電郵地址").isVisible()) {
        await page.getByLabel("電郵地址").fill(creds.email);
        await page.getByLabel("密碼", { exact: true }).fill(creds.password);
        await page
          .getByRole("button", { name: "登入工作空間", exact: true })
          .click();
      }
      await page.getByLabel("電郵地址").waitFor({ state: "hidden" });
      await nav("tables");
      await page
        .getByRole("button", { name: "建立資料表", exact: true })
        .click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Clients");
      await modal().getByRole("button", { name: "新增欄位" }).click();
      await modal().getByLabel("欄位名稱").nth(1).fill("Status");
      await modal().getByLabel("欄位識別碼").nth(1).fill("status");
      await modal().getByLabel("欄位類型").nth(1).selectOption("select");
      await modal().getByLabel("選項", { exact: true }).fill("New, Active");
      await modal().getByRole("button", { name: "新增欄位" }).click();
      await modal().getByLabel("欄位名稱").nth(2).fill("When");
      await modal().getByLabel("欄位識別碼").nth(2).fill("when");
      await modal().getByLabel("欄位類型").nth(2).selectOption("datetime");
      await save("儲存");
      await page
        .getByRole("button", { name: new RegExp(prefix + " Clients") })
        .click();
      await page
        .getByRole("button", { name: "新增記錄", exact: true })
        .first()
        .click();
      await modal()
        .getByLabel("名稱 *", { exact: true })
        .fill(prefix + " Client A");
      await modal()
        .getByLabel(/^Status/)
        .selectOption("New");
      await modal()
        .getByLabel("When", { exact: true })
        .fill("2026-10-20T10:30");
      await save("儲存記錄");
      await page.getByRole("button", { name: "看板", exact: true }).click();
      await page.getByRole("button", { name: "保存視圖", exact: true }).click();
      await page.getByRole("status").filter({ hasText: "已保存" }).waitFor();
      const tables = await api("/resources/table");
      const table = tables.find((x) => x.name === prefix + " Clients");
      assert(table);
      assert.equal(table.data.default_view, "board");
      const recs = await api("/tables/" + table.id + "/records");
      assert.equal(recs.length, 1);
      assert.equal(
        new Date(recs[0].data.values.when).toISOString(),
        "2026-10-20T10:30:00.000Z",
      );
      await page.reload();
      await page
        .getByRole("button", { name: new RegExp(prefix + " Clients") })
        .click();
      await page.waitForFunction(() =>
        document
          .querySelector(".business-view-tabs button.active")
          ?.textContent.includes("看板"),
      );
      await page
        .locator(".kanban-card")
        .filter({ hasText: prefix + " Client A" })
        .dragTo(
          page
            .locator(".kanban-column")
            .filter({ has: page.locator("header", { hasText: "Active" }) }),
        );
      await expect
        .poll(
          async () =>
            (await api("/tables/" + table.id + "/records"))[0].data.values
              .status,
        )
        .toBe("Active");
      console.log(
        "PASS table schema/create/datetime/persisted view/kanban move",
      );
      await nav("tasks");
      await page
        .getByRole("button", { name: "新增任務", exact: true })
        .first()
        .click();
      await modal()
        .getByLabel("任務名稱")
        .fill(prefix + " Task");
      const users = await api("/users");
      await modal()
        .getByLabel("負責人", { exact: true })
        .selectOption(users[0].id);
      await save("儲存");
      const taskrow = page
        .locator(".task-row")
        .filter({ hasText: prefix + " Task" });
      await taskrow
        .getByRole("button", { name: "完成任務", exact: true })
        .click();
      await taskrow.getByText("已完成", { exact: true }).waitFor();
      const tasks = await api("/resources/task");
      assert.equal(
        tasks.find((x) => x.name === prefix + " Task").data.status,
        "completed",
      );
      console.log("PASS task create/assignee/complete");
      await nav("workdays");
      await page
        .getByRole("button", { name: "記錄今日工作", exact: true })
        .click();
      await modal()
        .getByLabel("標題")
        .fill(prefix + " Daily");
      await modal().getByLabel(/^成果/).fill("Completed client follow up");
      await modal()
        .getByLabel(/^待辦及下一步/)
        .fill(prefix + " Todo");
      await save("儲存工作記錄");
      const daily = page
        .locator(".workday-card")
        .filter({ hasText: prefix + " Daily" });
      await daily.getByRole("checkbox").check();
      await expect(daily.getByRole("checkbox")).not.toBeDisabled();
      let days = await api("/resources/workday");
      assert.equal(
        days.find((x) => x.name === prefix + " Daily").data.todos[0].completed,
        true,
      );
      await daily.getByRole("button", { name: "檢視及編輯" }).click();
      await modal().getByLabel(/^成果/).fill("Updated outcome");
      await save("儲存工作記錄");
      days = await api("/resources/workday");
      assert.equal(
        days.find((x) => x.name === prefix + " Daily").data.todos[0].completed,
        true,
      );
      console.log("PASS workday/checkable todo/completion retained on edit");
      await nav("calendar");
      await page.getByRole("button", { name: "新增日程", exact: true }).click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Meeting");
      await modal()
        .getByLabel("開始", { exact: true })
        .fill("2026-10-20T09:00");
      await modal()
        .getByLabel("結束", { exact: true })
        .fill("2026-10-20T10:00");
      await modal().getByLabel(/^重複/).selectOption("FREQ=WEEKLY");
      await save("儲存日程");
      const events = await api("/resources/event");
      assert.equal(
        events.find((x) => x.name === prefix + " Meeting").data.rrule,
        "FREQ=WEEKLY",
      );
      const settings = await api("/settings");
      if (!settings.services.google_calendar) {
        await page.getByRole("button", { name: /連接 Google/ }).click();
        await page.getByRole("alert").waitFor();
        assert((await page.getByRole("alert").textContent()).length > 10);
      }
      console.log(
        "PASS calendar recurrence create / missing provider truthfully reports error",
      );
      await nav("forms");
      await page
        .getByRole("button", { name: "建立表單", exact: true })
        .first()
        .click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Form");
      await modal()
        .getByLabel(/^提交對象/)
        .selectOption("public");
      await save("儲存");
      const formrow = page.locator("tr").filter({ hasText: prefix + " Form" });
      await formrow.getByRole("button", { name: "發佈", exact: true }).click();
      await page.getByRole("status").filter({ hasText: "已發佈" }).waitFor();
      const forms = await api("/resources/form");
      const form = forms.find((x) => x.name === prefix + " Form");
      assert(form.data.public_url);
      const publicCtx = await browser.newContext({ baseURL });
      const publicPage = await publicCtx.newPage();
      const publicUrl = new URL(form.data.public_url, baseURL);
      await publicPage.goto(publicUrl.pathname + publicUrl.search);
      await publicPage
        .getByLabel("名稱 *", { exact: true })
        .fill(prefix + " Public submission");
      await publicPage
        .getByRole("button", { name: "提交", exact: true })
        .click();
      await publicPage.getByText("提交成功", { exact: false }).waitFor();
      assert(
        (await api("/resources/submission")).some((x) =>
          JSON.stringify(x.data).includes(prefix + " Public submission"),
        ),
      );
      await publicCtx.close();
      console.log(
        "PASS form create/publish/anonymous token submission persisted",
      );
      console.log(JSON.stringify({ prefix, errors }));
      assert.deepEqual(errors, []);
      await nav("tables");
      await page
        .getByRole("button", { name: new RegExp(prefix + " Clients") })
        .click();
      await page
        .locator(".kanban-card")
        .filter({ hasText: prefix + " Client A" })
        .waitFor();
      await page.screenshot({
        path: testInfo.outputPath("table-overview.png"),
        fullPage: true,
      });
      await page.locator(".global-search").click();
      await page
        .getByLabel("全域搜尋", { exact: true })
        .fill(prefix + " Client A");
      await page
        .locator(".search-result")
        .filter({ hasText: prefix + " Client A" })
        .first()
        .click();
      await modal()
        .getByRole("heading", { name: "編輯記錄", exact: true })
        .waitFor();
      assert.equal(
        await modal().getByLabel("名稱 *", { exact: true }).inputValue(),
        prefix + " Client A",
      );
      await modal().getByRole("button", { name: "關閉", exact: true }).click();
      await page.setViewportSize({ width: 390, height: 844 });
      await nav("tasks");
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        ),
        false,
      );
    } catch (e) {
      console.log("FAIL", e.message);
      console.log("URL", page.url());
      await page
        .screenshot({
          path: testInfo.outputPath("blueprint-business-live-fail.png"),
          fullPage: true,
        })
        .catch(() => {});
      throw e;
    } finally {
      await ctx.close();
    }
  });
  test("Yjs collaboration, safe editors, approval workflow, DOCX and governance", async ({
    browser,
    baseURL,
  }, testInfo) => {
    const ctx = await browser.newContext({
      baseURL,
      viewport: { width: 1512, height: 982 },
    });
    const page = await ctx.newPage();
    page.setDefaultTimeout(12000);
    const prefix = "BP-BIZ-E2E2-" + Date.now().toString(36),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const creds = {
      email: process.env.BLUEPRINT_E2E_EMAIL,
      password: process.env.BLUEPRINT_E2E_PASSWORD,
    };
    const api = async (path, body, method = body ? "POST" : "GET") =>
      page.evaluate(
        async ({ path, body, method }) => {
          const r = await fetch("/api/v1" + path, {
            method,
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/json",
              "X-Blueprint-Request": "1",
            },
            ...(body ? { body: JSON.stringify(body) } : {}),
          });
          const data = await r.json();
          if (!r.ok) throw new Error(r.status + " " + JSON.stringify(data));
          return data;
        },
        { path, body, method },
      );
    const poll = async (fn, timeout = 15000) => {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        if (await fn()) return;
        await page.waitForTimeout(150);
      }
      throw new Error("poll timed out");
    };
    const nav = async (section) => {
      await page.goto(baseURL + "/#" + section);
      await page
        .locator(".business-page,.workflow-editor,.note-editor,.canvas-editor")
        .first()
        .waitFor();
    };
    const modal = () => page.getByRole("dialog");
    const save = async (label) => {
      await modal().getByRole("button", { name: label, exact: true }).click();
      await modal().waitFor({ state: "hidden" });
    };
    try {
      await page.goto(baseURL);
      await page.getByLabel("電郵地址").waitFor();
      await page.getByLabel("電郵地址").fill(creds.email);
      await page.getByLabel("密碼", { exact: true }).fill(creds.password);
      await page
        .getByRole("button", { name: "登入工作空間", exact: true })
        .click();
      await page.getByLabel("電郵地址").waitFor({ state: "hidden" });
      await nav("notes");
      await page.getByRole("button", { name: "新增筆記", exact: true }).click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Note");
      await save("建立");
      await page.locator(".collaboration-status.connected").waitFor();
      const noteText =
        "# Team plan\n\n- [x] Finished\n\n| Name | Status |\n| --- | --- |\n| Client | Active |\n\n<script>window.bpInjected=true</script>\n[unsafe](javascript:alert(1))";
      await page.getByLabel("筆記內容").fill(noteText);
      let note;
      await poll(async () => {
        note = (await api("/resources/note")).find(
          (x) => x.name === prefix + " Note",
        );
        return note?.data.content === noteText;
      });
      const second = await ctx.newPage();
      await second.goto(baseURL + "/#notes");
      await second
        .getByRole("button", { name: prefix + " Note", exact: true })
        .click();
      await second.locator(".collaboration-status.connected").waitFor();
      assert.equal(await second.getByLabel("筆記內容").inputValue(), noteText);
      await second
        .getByLabel("筆記內容")
        .fill(noteText + "\n\nSecond member edit");
      await poll(async () =>
        (await page.getByLabel("筆記內容").inputValue()).includes(
          "Second member edit",
        ),
      );
      await page.getByRole("button", { name: "閱讀模式", exact: true }).click();
      await page.locator(".note-editor table").first().waitFor();
      assert.equal(await page.evaluate(() => window.bpInjected), undefined);
      assert.equal(await page.locator('a[href^="javascript:"]').count(), 0);
      await second.close();
      await page.screenshot({
        path: testInfo.outputPath("blueprint-note-live.png"),
        fullPage: true,
      });
      console.log(
        "PASS Notes real Yjs WebSocket persistence / two-browser convergence / GFM table / XSS-safe preview",
      );
      await nav("canvas");
      await page.getByRole("button", { name: "新增白板", exact: true }).click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Canvas");
      await save("建立");
      await page.locator(".collaboration-status.connected").waitFor();
      await page.getByRole("button", { name: "便利貼", exact: true }).click();
      await page
        .locator("svg.canvas-board")
        .click({ position: { x: 250, y: 180 } });
      await modal().waitFor();
      await modal()
        .locator("textarea")
        .fill(prefix + " <img src=x onerror=alert(1)>");
      await save("儲存");
      let canvas;
      await poll(async () => {
        canvas = (await api("/resources/canvas")).find(
          (x) => x.name === prefix + " Canvas",
        );
        return canvas?.data.elements?.some((e) => e.text?.includes(prefix));
      });
      assert.equal(await page.locator(".canvas-board img").count(), 0);
      await page.screenshot({
        path: testInfo.outputPath("blueprint-canvas-live.png"),
        fullPage: true,
      });
      console.log(
        "PASS Canvas real Yjs element creation/text persistence / escaped text",
      );
      async function openSearch(name) {
        await page.locator(".global-search").click();
        await page.getByLabel("全域搜尋", { exact: true }).fill(name);
        await page
          .locator(".search-result")
          .filter({ hasText: name })
          .first()
          .click();
      }
      await openSearch(prefix + " Note");
      await page
        .locator(".note-editor .document-title")
        .filter({ hasText: prefix + " Note" })
        .waitFor();
      const secondNote = await api("/resources", {
        kind: "note",
        name: prefix + " Second note",
        data: { content: "Independent document" },
        visibility: "company",
      });
      await openSearch(secondNote.name);
      await page
        .locator(".note-editor .document-title")
        .filter({ hasText: secondNote.name })
        .waitFor();
      await page.getByLabel("筆記內容").waitFor();
      await poll(
        async () =>
          (await page.getByLabel("筆記內容").inputValue()) ===
          "Independent document",
      );
      await nav("workflows");
      await page
        .getByRole("button", { name: "建立工作流程", exact: true })
        .click();
      await page.getByLabel("工作流程名稱").fill(prefix + " Approval");
      await page
        .locator(".workflow-palette button")
        .filter({ hasText: "條件分支" })
        .click();
      await page.getByLabel("資料路徑", { exact: true }).fill("input.amount");
      await page.getByLabel(/^比較方式/).selectOption("gte");
      await page.getByLabel("比較值", { exact: true }).fill("100");
      await page
        .locator(".workflow-palette button")
        .filter({ hasText: "人工審批" })
        .click();
      await page
        .getByLabel("審批標題", { exact: true })
        .fill(prefix + " Review");
      await page
        .locator(".workflow-palette button")
        .filter({ hasText: "建立任務" })
        .click();
      await page
        .getByLabel("任務名稱", { exact: true })
        .fill(prefix + " After approval");
      await page.getByRole("button", { name: "啟用流程", exact: true }).click();
      await page.getByRole("button", { name: "停用", exact: true }).waitFor();
      const workflow = (await api("/resources/workflow")).find(
        (x) => x.name === prefix + " Approval",
      );
      assert(
        workflow.data.edges.some(
          (e) => e.source.startsWith("condition") && e.source_handle === "true",
        ),
      );
      await page.getByRole("button", { name: "執行", exact: true }).click();
      await modal()
        .getByLabel(/輸入資料（JSON）/)
        .fill('{"amount":150}');
      await modal()
        .getByRole("button", { name: "開始執行", exact: true })
        .click();
      await modal().getByText("執行已建立", { exact: true }).waitFor();
      const runId = await modal().locator("code").textContent();
      await save("完成");
      await nav("runs");
      await poll(async () => {
        const run = await api("/runs/" + runId);
        return (
          run.data.status === "waiting_approval" ||
          run.data.status === "waiting"
        );
      });
      await page
        .getByRole("button", { name: /重新整理/ })
        .first()
        .click();
      await page
        .locator("tr")
        .filter({ hasText: runId.slice(0, 8) })
        .click();
      await modal().getByRole("button", { name: "批准", exact: true }).click();
      await poll(
        async () => (await api("/runs/" + runId)).data.status === "completed",
      );
      assert(
        (await api("/resources/task")).some(
          (x) => x.name === prefix + " After approval",
        ),
      );
      console.log(
        "PASS visual workflow condition true branch / publish / run / approval / persisted task",
      );
      await nav("docgen");
      await page
        .getByLabel("文件名稱", { exact: true })
        .fill(prefix + " Document");
      await page
        .getByLabel(/^範本內容/)
        .fill(
          "# {{ title }}\n\nDear {{ client }},\n\n| Item | Quantity |\n| --- | --- |\n| Service | 2 |",
        );
      await page
        .locator(".docgen-sidebar textarea")
        .fill('{"title":"Service proposal","client":"Blueprint"}');
      await page.getByRole("button", { name: "生成文件", exact: true }).click();
      await page.getByText("文件已準備好", { exact: true }).waitFor();
      const generated = (await api("/resources/file")).find((x) =>
        x.name.startsWith(prefix + " Document"),
      );
      assert(generated);
      const download = await ctx.request.get(
        "/api/v1/files/" + generated.id + "/download",
      );
      assert.equal(download.status(), 200);
      assert.equal((await download.body()).subarray(0, 2).toString(), "PK");
      await page
        .getByRole("button", { name: "儲存為範本", exact: true })
        .click();
      await page
        .getByRole("status")
        .filter({ hasText: "範本已儲存" })
        .waitFor();
      console.log(
        "PASS DOCX generation / real ZIP download / saved reusable template",
      );
      await nav("retention");
      await page
        .getByRole("button", { name: "建立保存政策", exact: true })
        .click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Retain");
      await modal()
        .getByLabel("至少保存至", { exact: true })
        .fill("2027-10-20T10:00");
      await modal()
        .getByRole("checkbox", { name: new RegExp(prefix + " Document") })
        .check();
      await save("儲存政策");
      assert(
        (await api("/resources/retention")).some(
          (x) => x.name === prefix + " Retain",
        ),
      );
      console.log("PASS scoped retention policy persisted");
      await nav("security");
      await page
        .getByRole("button", { name: "資料防護規則", exact: true })
        .click();
      await page.getByRole("button", { name: "新增規則", exact: true }).click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Classification");
      await modal()
        .getByRole("checkbox", { name: new RegExp(prefix + " Document") })
        .check();
      await save("儲存政策");
      assert(
        (await api("/resources/classification")).some(
          (x) => x.name === prefix + " Classification",
        ),
      );
      console.log("PASS scoped classification policy persisted");
      const settings = await api("/settings");
      if (!settings.services.documenso) {
        await nav("sign");
        await page
          .getByRole("button", { name: "建立簽署請求", exact: true })
          .first()
          .click();
        await modal()
          .getByLabel("請求名稱")
          .fill(prefix + " Sign");
        await modal()
          .getByRole("radio", { name: new RegExp(prefix + " Document") })
          .check();
        await modal().getByLabel("簽署人姓名").fill("Test Signer");
        await modal().getByLabel("簽署人電郵").fill("signer@example.invalid");
        await modal()
          .getByRole("button", { name: "建立簽署請求", exact: true })
          .click();
        await modal().getByRole("alert").waitFor();
        assert(
          (await modal().getByRole("alert").textContent()).includes(
            "Documenso",
          ),
        );
        await modal()
          .getByRole("button", { name: "關閉", exact: true })
          .click();
      }
      await nav("archives");
      await page
        .getByRole("button", { name: "建立歸檔", exact: true })
        .first()
        .click();
      await modal()
        .getByLabel("名稱", { exact: true })
        .fill(prefix + " Archive");
      await modal()
        .getByRole("checkbox", { name: new RegExp(prefix + " Document") })
        .check();
      await save("儲存政策");
      assert(
        (await api("/resources/archive")).some(
          (item) => item.name === prefix + " Archive",
        ),
      );
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ prefix, errors }));
    } catch (e) {
      console.log("FAIL", e.message);
      console.log("URL", page.url());
      await page
        .screenshot({
          path: testInfo.outputPath("blueprint-business-live2-fail.png"),
          fullPage: true,
        })
        .catch(() => {});
      throw e;
    } finally {
      await ctx.close();
    }
  });
});

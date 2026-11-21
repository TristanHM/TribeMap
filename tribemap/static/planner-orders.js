/* Orders for the selected unit and plan. */
const PlannerOrders = (() => {
  const sections = ["Activities", "Transfers", "Skills", "GM Actions", "Review"];
  const outcomes = {
    "Hunting": "Provisions",
    "Furrier": "Furs, skins and provisions",
    "Herding": "Animals",
    "Fishing": "Provisions",
    "Woodwork · Clubs": "Use wood from the forest",
    "Woodwork · Wagons": "10 workers and 6 starting logs per wagon",
    "Forestry": "Logs for future turns",
    "Mining · Salt": "Salt",
    "Curing": "Skins",
    "Tanning": "Skins",
  };
  const simpleActivities = {
    "HUNTING|PROVS|NONE": "Hunting", "FISHING|PROVS|NONE": "Fishing",
    "FURRIER|FURRIER|NONE": "Furrier", "HERDING|ANIMALS|NONE": "Herding",
    "WOODWORK|CLUB|FOREST": "Woodwork · Clubs", "WOODWORK|WAGON|NONE": "Woodwork · Wagons",
    "FORESTRY|LOG|NONE": "Forestry", "CURING|SKIN|NONE": "Curing", "TANNING|SKIN|NONE": "Tanning",
  };
  const forest = new Set(["CH", "D", "DF", "DH", "JG", "JH", "UJS", "LCM", "LJM", "UM"]);
  const waterResources = /\b(lake|ocean|river|coast)\b/i;
  let catalogSource, catalog;
  let view = sections[0];

  const key = (u, plan) => `worksheet.${plan.turn}.${plan.variant}.${u.unit}`;
  const endpoint = (plan, suffix) => `/api/planner/${plan.turn}/${suffix}?variant=${encodeURIComponent(plan.variant)}`;
  const pending = new Map();
  const reviews = new WeakMap();

  function draft(u, plan) {
    const id = key(u, plan);
    const saved = plan.worksheet?.[u.unit];
    const recovery = recall(id + ".pending", null);
    const result = structuredClone(pending.get(id)?.orders ||
      (recovery?.revision === (saved?.revision || 0) ? recovery.orders : null) ||
      saved?.orders || recall(id, null) ||
      { rows: [], cap: null, skills: u.kind === "Tribe" ? ["", "", ""] : [], gm: [] });
    if (typeof result.gm === "string") result.gm = result.gm ? [result.gm] : [];
    result.gm ??= [];
    result.rows ??= [];
    result.skills ??= u.kind === "Tribe" ? ["", "", ""] : [];
    result.transfers ??= [];
    result.comments ??= [];
    result.research ??= [];
    return result;
  }

  const transferAnalyses = new WeakMap();
  const wagonCount = (order) => sum(order.rows.filter(([name, , , fields]) => fields?.activity
    ? fields.activity.toUpperCase() === "WOODWORK" && fields.item?.toUpperCase() === "WAGON"
    : name === "Woodwork · Wagons").map(([, workers]) => Math.floor(workers / 10)));

  function observedOutputs(row, u, plan) {
    if (u.new) return null;
    const codes = activityCatalog(plan).get(row[0]);
    const fields = row[3];
    const activity = fields?.activity?.toUpperCase() || codes?.activity;
    const item = fields?.item?.toUpperCase() || codes?.item;
    const distinction = fields?.distinction?.toUpperCase() || codes?.distinction || "NONE";
    const seasonal = ["HUNTING", "FURRIER", "MINING"].includes(activity);
    const matches = (plan.activity_observations || []).filter((result) =>
      result.activity === activity && result.item === item && result.distinction === distinction &&
      result.level === (u.skills[result.skill] || 0) &&
      (!seasonal || (result.season?.toLowerCase() === "winter") === plan.calendar.winter));
    const local = matches.filter((result) => result.terrain && result.terrain === u.where?.terrain);
    const samples = local.length ? local : matches;
    if (!samples.length) return null;
    const goods = new Map();
    for (const sample of samples) {
      for (const [good, qty] of Object.entries(sample.goods)) {
        const total = goods.get(good) || { qty: 0, workers: 0 };
        total.qty += qty;
        total.workers += sample.workers;
        goods.set(good, total);
      }
    }
    const turns = [...new Set(samples.map((sample) => sample.turn))];
    const { skill, level } = samples[0];
    return {
      outputs: [...goods].map(([good, total]) => [good, total.qty / total.workers, "per worker"]),
      source: `${plan.skill_info[skill]?.skill || skill} ${level} · ${samples.length} ${samples.length === 1 ? "observation" : "observations"} · ${turns.join(", ")}${local.length ? " · same terrain" : ""}`,
    };
  }

  function activityOutputs(row, u, plan) {
    if (row[3]?.outputs) return { outputs: row[3].outputs, source: "Manual estimate" };
    if (row[2] != null) return { outputs: [["PROVS", row[2], "per worker"]], source: "Manual estimate" };
    return observedOutputs(row, u, plan) || { outputs: [], source: "No matching observations" };
  }

  function plannedOutput([code, amount, basis], workers, plan) {
    const good = code.trim().toUpperCase();
    const qty = workers ? Math.round((amount || 0) * (basis === "per worker" ? workers : 1)) : 0;
    return [good, qty, plan.goods_catalog?.[good]?.short || good];
  }

  function transferAnalysis(plan) {
    if (!transferAnalyses.has(plan)) {
      const orders = new Map(plan.units.map((u) => [u.unit, draft(u, plan)]));
      transferAnalyses.set(plan, PlannerTransfers.analyse(plan, orders, (order, unit) => ({
        yields: order.rows.flatMap((row) => activityOutputs(row, unit, plan).outputs.map((output) => {
          const [code, qty, name] = plannedOutput(output, row[1], plan);
          const activity = row[3]?.activity || activityCatalog(plan).get(row[0])?.activity;
          return { code, qty, name, deferred: name.toUpperCase() === "LOG" && activity?.toUpperCase() === "FORESTRY" };
        })),
        materials: [["LOG", wagonCount(order) * 6], ...order.rows.flatMap((row) => row[3]?.materials || [])],
      })));
    }
    return transferAnalyses.get(plan);
  }

  function saveDraft(u, plan, orders) {
    const id = key(u, plan);
    const entry = pending.get(id) || { revision: plan.worksheet?.[u.unit]?.revision || 0 };
    Object.assign(entry, { orders, plan, unit: u.unit, error: "" });
    pending.set(id, entry);
    store(id + ".pending", { revision: entry.revision, orders });
    transferAnalyses.delete(plan);
    reviews.delete(plan);
    writePending(id, entry);
  }

  function status() {
    const node = document.getElementById("plSaveStatus");
    if (!node) return;
    const error = [...pending.values()].find((entry) => entry.error);
    const plan = state.planner;
    const recovery = plan?.units.some((u) => {
      const saved = recall(key(u, plan) + ".pending", null);
      return saved && saved.revision !== (plan.worksheet?.[u.unit]?.revision || 0);
    });
    node.textContent = error ? error.error : pending.size ? "Saving draft…" : recovery
      ? "An earlier unsaved edit remains in this browser." : "Drafts save with the plan";
    node.classList.toggle("pl-flag", Boolean(error));
  }

  function writePending(id, entry) {
    if (entry.sending) return entry.sending;
    entry.sending = (async () => {
      while (true) {
        const orders = entry.orders;
        try {
          const { revision } = await api(endpoint(entry.plan, `worksheet/${entry.unit}`),
            { method: "PUT", json: { orders, revision: entry.revision } });
          entry.revision = revision;
          (entry.plan.worksheet ??= {})[entry.unit] = { revision, orders };
          if (orders === entry.orders) {
            pending.delete(id);
            store(id, null);
            store(id + ".pending", null);
            break;
          }
          store(id + ".pending", { revision, orders: entry.orders });
        } catch (err) {
          entry.error = `Draft kept in browser · ${err.message}`;
          break;
        }
      }
    })().finally(() => { entry.sending = null; status(); });
    status();
    return entry.sending;
  }

  async function flush(plan) {
    for (const u of plan?.units || []) {
      const id = key(u, plan);
      if (!pending.has(id) && (recall(id + ".pending", null)?.revision === (plan.worksheet?.[u.unit]?.revision || 0) || (!plan.worksheet?.[u.unit] &&
          recall(id, null)))) {
        saveDraft(u, plan, draft(u, plan));
      }
    }
    await Promise.all([...pending].map(([id, entry]) => writePending(id, entry)));
    if (pending.size) throw new Error([...pending.values()][0].error || "Drafts are still saving.");
  }

  function activityCatalog(plan) {
    if (catalogSource === plan.activity_info && catalog) return catalog;
    catalogSource = plan.activity_info;
    catalog = new Map();
    for (const item of plan.activity_info || []) {
      if (item.activity === "SCOUTING") continue;  // derived from the routes
      const key = [item.activity, item.item, item.distinction].join("|");
      const title = [item.activity, item.item, item.distinction].filter((value, index) =>
        value && value !== "NONE" && (index === 0 || value !== item.activity)).map((value) =>
          value.toLowerCase().replace(/(^|[\s&/()-])\p{L}/gu, (c) => c.toUpperCase())).join(" · ");
      catalog.set(simpleActivities[key] || title, item);
    }
    return catalog;
  }

  function locationSuggestions(u, plan) {
    const where = u.where;
    const suggestions = new Map();
    if (!where) return suggestions;
    for (const resource of where.resources) {
      const good = resource.toUpperCase() === "DIAMONDS" ? "DIAMOND" : resource.toUpperCase();
      const mining = [...activityCatalog(plan)].find(([, item]) => item.activity === "MINING" && item.item === good);
      if (mining) suggestions.set(mining[0], `${resource} deposit at the start hex`);
    }
    if (forest.has(where.terrain)) {
      suggestions.set("Forestry", "Cut logs in this wooded hex");
      suggestions.set("Woodwork · Clubs", "Make clubs from standing timber");
    }
    if (where.river || where.water.length || Object.values(where.hexsides).flat().some((edge) => waterResources.test(edge))) {
      suggestions.set("Fishing", "Water borders the start hex");
    }
    if (u.herders.total) suggestions.set("Herding", `${fmt(u.herders.total)} herders needed for this unit's animals`);
    return suggestions;
  }

  const fmt = (value) => value == null ? "Unknown" : num(value);
  const pounds = (value) => value == null ? "Unknown" : `${fmt(value)} lb`;
  const sum = (values) => values.reduce((total, value) => total + value, 0);
  const parent = (u) => u.kind === "Element" ? (u.parent || u.unit.split("e")[0]) : u.unit;

  function totals(u, plan, orders = draft(u, plan)) {
    const start = u.inventory["Finished Goods"]?.Provs || 0;
    const scouts = sum(u.plan.scouts.map((party) => party.scouts || 0));
    const ordinary = sum(orders.rows.filter(([name]) => activityCatalog(plan).get(name)?.activity !== "KILLING")
      .map(([, workers]) => workers));
    const logistics = transferAnalysis(plan).get(u.unit);
    const warriors = Math.max(0, (u.people.Warriors || 0) - logistics.reservedWarriors);
    const actives = Math.max(0, (u.people.Actives || 0) - logistics.reservedActives);
    const assigned = scouts + ordinary;
    const workers = warriors + actives;
    const parties = sum(plan.units.filter((other) => parent(other) === parent(u)).map((other) => other.plan.scouts.length));
    const herders = sum(orders.rows.filter(([activity]) => activity === "Herding").map(([, quantity]) => quantity));
    const load = u.carry.weight == null ? null : Math.round(u.carry.weight + logistics.weightDelta);
    const capacity = u.mover === "mounted" ? u.carry.mounted : u.carry.walking;
    return { scouts, parties, assigned, workers, free: workers - assigned, herders, start, afterActivities: logistics.before,
      freeActives: Math.max(0, actives - ordinary),
      freeWarriors: Math.max(0, warriors - scouts - Math.max(0, ordinary - actives)),
      shortScouts: Math.max(0, scouts - logistics.movingWarriors),
      carried: logistics.carried, load, capacity, margin: load == null || capacity == null ? null : capacity - load,
      afterMeal: u.people.People == null && !u.creation ? null : logistics.finalProvs - (u.people.People || 0) - logistics.peopleDelta };
  }

  function balance(u, plan) {
    return u.new ? "—" : fmt(totals(u, plan).free);
  }

  function budget(u, plan) {
    const t = totals(u, plan);
    const stat = (label, value, flagged = false) =>
      `<div class="pl-stat${flagged ? " pl-flag" : ""}"><span>${label}</span><strong>${value}</strong></div>`;
    return `
      ${stat("Workers assigned", u.new ? fmt(t.assigned) : `${fmt(t.assigned)} / ${fmt(t.workers)}`)}
      ${stat("Workers free", balance(u, plan), !u.new && t.free < 0)}
      ${stat("Unspent warriors", u.new ? "—" : fmt(t.freeWarriors), !u.new && t.shortScouts > 0)}
      ${stat("Unspent actives", u.new ? "—" : fmt(t.freeActives))}
      ${stat("Herders assigned / needed", `${fmt(t.herders)} / ${fmt(u.herders.total)}`, t.herders < u.herders.total)}
      ${stat("Shared scout parties", `${t.parties} / 8`, t.parties > 8)}
      <div class="pl-phase">
        <h4>Before movement</h4>
        ${stat("Provisions at start", fmt(t.start))}
        ${stat("After activities · est.", fmt(t.afterActivities))}
        ${stat('<button class="pl-inline" type="button" data-pl-section="Transfers">After transfers · est.</button>', fmt(t.carried))}
        ${stat("Load after BM transfers · est.", pounds(t.load))}
        ${stat("Reported capacity", pounds(t.capacity))}
        ${stat(t.margin < 0 ? "Estimated excess" : "Estimated spare capacity", pounds(t.margin == null ? null : Math.abs(t.margin)), t.margin < 0)}
      </div>
      <div class="pl-phase">${stat("Provisions after eating · est.", fmt(t.afterMeal), t.afterMeal < 0)}</div>
      <small class="muted">Load includes estimated output, materials and transfers with known weights. Capacity stays at its reported level.</small>`;
  }

  const numberInput = (label, attr, value, step = "1") =>
    `<input type="number" min="0" max="999999999999999" step="${step}" aria-label="${esc(label)}" ${attr} value="${value ?? ""}">`;

  function activityOptions(label, extra, index, catalog) {
    const codes = catalog.get(label);
    const fields = ["activity", "item", "distinction"].map((field) => `<label>${field}
      <input data-pl-activity-field="${field}" data-pl-activity-index="${index}"
        value="${esc(extra[field] ?? codes?.[field] ?? (field === "activity" ? label.toUpperCase() : ""))}"></label>`).join("");
    const pairs = Object.entries({ implements: "implement", materials: "material" }).map(([field, name]) =>
      `<div>${(extra[field] || []).map(([good, qty], i) => `<div class="pl-editor-actions">
        <input placeholder="${name}" aria-label="${name} ${i + 1}" data-pl-pair-code="${i}" data-pl-list="${field}"
          data-pl-activity-index="${index}" value="${esc(good)}">
        ${numberInput(`${name} ${i + 1} quantity`, `data-pl-pair-qty="${i}" data-pl-list="${field}" data-pl-activity-index="${index}"`, qty)}
        <button type="button" data-pl-action="pair-remove" data-pl-list="${field}" data-pl-activity-index="${index}" data-pl-pair-index="${i}">Remove</button>
      </div>`).join("")}<button type="button" data-pl-action="pair-add" data-pl-list="${field}" data-pl-activity-index="${index}">Add ${name}</button></div>`).join("");
    return `<details class="pl-activity-options"><summary>Workbook fields${extra.implements?.length ? ` · ${extra.implements.length} implements` : ""}</summary>
      <div class="pl-editor-actions">${fields}</div>${pairs}
      <small class="muted">Added materials reduce estimated stock. Wagon logs are calculated automatically.</small></details>`;
  }

  function outputSummary(row, u, plan) {
    const { outputs } = activityOutputs(row, u, plan);
    const amounts = outputs.map((output) => plannedOutput(output, row[1], plan))
      .filter(([code, qty]) => code && qty).map(([, qty, name]) => `${fmt(qty)} ${name}`);
    return amounts.length ? amounts.join(" · ") : outputs.length ? "Edit estimate" : "Add estimate";
  }

  function activityEstimate(row, index, u, plan) {
    const { outputs, source } = activityOutputs(row, u, plan);
    const fields = outputs.map(([code, qty, basis], i) => `<div class="pl-output-fields">
      <input value="${esc(code)}" list="plOutputGoods" placeholder="Good"
        aria-label="${esc(row[0])} output ${i + 1} good" data-pl-output-field="0" data-pl-output-index="${i}" data-pl-activity-index="${index}">
      ${numberInput(`${row[0]} output ${i + 1} amount`,
        `data-pl-output-field="1" data-pl-output-index="${i}" data-pl-activity-index="${index}"`, Number(qty.toPrecision(10)), "any")}
      <select aria-label="${esc(row[0])} output ${i + 1} basis" data-pl-output-field="2" data-pl-output-index="${i}" data-pl-activity-index="${index}">
        <option${basis === "total" ? " selected" : ""}>total</option><option${basis === "per worker" ? " selected" : ""}>per worker</option>
      </select>
      <button type="button" aria-label="Remove output ${i + 1}" data-pl-action="output-remove" data-pl-output-index="${i}" data-pl-activity-index="${index}">×</button>
      </div>`).join("");
    return `<details class="pl-activity-output" data-pl-output-row="${index}">
      <summary data-pl-output-total="${index}">${esc(outputSummary(row, u, plan))}</summary>
      <small class="muted" data-pl-output-source="${index}">${esc(source)}</small>
      ${fields}
      <button type="button" data-pl-action="output-add" data-pl-activity-index="${index}">Add output</button>
      <button type="button" data-pl-action="output-reset" data-pl-activity-index="${index}"
        ${source === "Manual estimate" && observedOutputs(row, u, plan) ? "" : "hidden"}>Use observed rates</button>
    </details>`;
  }

  function activityRows(u, plan, orders) {
    const t = totals(u, plan, orders);
    const options = activityCatalog(plan);
    const suggestions = [...locationSuggestions(u, plan)];
    const optionNames = [...new Set([...suggestions.map(([name]) => name), ...options.keys()])];
    const rows = orders.rows.map((row, i) => {
      const [activity, workers, , extra = {}] = row;
      const isQuantity = (extra.activity || options.get(activity)?.activity || "").toUpperCase() === "KILLING";
      const workerAction = (action, label, description, unavailable = false) => `<button type="button"
        data-pl-action="worker-${action}" data-pl-worker-row="${i}"
        aria-label="${esc(`${activity}: ${description}`)}" title="${esc(description)}"${unavailable ? " disabled" : ""}>${label}</button>`;
      const input = numberInput(`${activity} ${isQuantity ? "quantity" : "workers"}`, `data-pl-workers="${i}"`, workers);
      return `<tr>
      <td><strong>${esc(activity)}</strong><small>${esc(outcomes[activity] || options.get(activity)?.outcome || (options.has(activity) ? "" : "Custom activity"))}</small>
      ${activityOptions(activity, extra, i, options)}</td>
      <td class="num">${isQuantity ? input : `<div class="pl-worker-stepper">
        ${workerAction("minus", "−", "Remove one worker", !workers)}${input}${workerAction("plus", "+", "Add one worker")}</div>
        <div class="pl-worker-bulk">${workerAction("half", "½", "Add half the remaining workers", t.free < 2)}
          ${workerAction("max", "Max", "Add all remaining workers", t.free < 1)}</div>`}</td>
      <td>${activityEstimate(row, i, u, plan)}</td>
      <td><button type="button" data-pl-remove="${i}" aria-label="Remove ${esc(activity)}">×</button></td>
    </tr>`;
    }).join("");
    return `<div class="pl-order-table"><table>
      <thead><tr><th>Activity</th><th class="num">Workers / qty</th><th>Estimated output</th><th></th></tr></thead>
      <tbody>${rows}<tr><td><strong>Scouting</strong><small>From ${u.plan.scouts.length} map routes</small></td>
        <td class="num">${fmt(t.scouts)}</td><td></td><td></td></tr></tbody>
      </table></div>
      <datalist id="plOutputGoods">${Object.keys(plan.goods_catalog || {}).sort().map((good) => `<option value="${esc(good)}"></option>`).join("")}</datalist>
      <div id="plWorkRemaining" class="pl-worker-remainder" aria-live="polite">${workerRemainder(u, t)}</div>
      <div class="pl-editor-actions">
        <form id="plActivityForm"><input id="plAddActivity" list="plActivities" aria-label="Find or type an activity" placeholder="Find or type an activity" required>
        <datalist id="plActivities">${optionNames.map((name) => `<option value="${esc(name)}"></option>`).join("")}</datalist>
        <button type="submit">Add activity</button></form>
        ${orders.rows.some(([name]) => name === "Hunting") ? '<button type="button" data-pl-action="hunt">Balance with Hunting</button>' : ""}
      </div>
      ${suggestions.length ? `<div class="pl-suggestions"><h4>Notable at ${esc(u.where.hex)}</h4>
        ${suggestions.map(([name, why]) => {
          const included = orders.rows.some(([existing]) => name === existing);
          return `<div>${included ? `<strong>${esc(name)}</strong>` : `<button type="button" data-pl-suggest="${esc(name)}">+ ${esc(name)}</button>`}
            <small class="muted">${esc(why)}${included ? " · planned" : ""}</small></div>`;
        }).join("")}</div>` : ""}`;
  }

  function workerRemainder(u, t) {
    if (u.new) return '<small class="muted">Worker balance needs a report for this unit.</small>';
    return `<strong class="${t.free < 0 ? "pl-flag" : ""}">${fmt(t.freeWarriors)} warriors and ${fmt(t.freeActives)} actives unspent</strong>
      <small class="muted">Scouting reserves warriors. Other activities use actives first in this estimate.</small>`;
  }

  function skillDetails(u, plan, orders) {
    const entries = Object.entries(plan.skill_info);
    const selected = orders.skills.map((name) => entries.find(([code, s]) => code === name || s.skill === name));
    const silver = Object.values(u.inventory).find((section) => "Silver" in section)?.Silver;
    return selected.map((entry, i) => {
      if (!entry || !orders.skills[i]) return null;
      const [code, skill] = entry;
      const level = (u.skills[code] || 0) + 1;
      const teacher = i === 2;
      const sameGroup = i === 1 && selected[0]?.[1].group === skill.group;
      const multiplier = i === 0 ? 1 : sameGroup ? .25 : .5;
      const cost = teacher ? 300 * level : 0;
      const problems = [];
      if (selected.slice(0, i).some((earlier) => earlier?.[0] === code)) problems.push("This skill is already attempted earlier in the turn.");
      if (level > 10) problems.push("This skill is already at level 10.");
      if (i && !selected.slice(0, i).every(Boolean)) problems.push("Fill the earlier attempts first.");
      if (teacher && (skill.group !== "A" || selected.slice(0, 2).some((earlier) => earlier && !["B", "C"].includes(earlier[1].group)))) {
        problems.push("A teacher needs a Group A skill after two Group B or C attempts.");
      }
      if (teacher && level > 7) problems.push("A teacher can only take a skill up to level 7.");
      if (teacher && silver != null && silver < cost) {
        problems.push(`Costs ${fmt(cost)} silver. This unit starts with ${fmt(silver)}; check its silver at skill time.`);
      }
      return { group: skill.group, level, chance: Math.max(0, (11 - level) * 10 * multiplier), cost, sameGroup, problems };
    });
  }

  function skillRows(u, plan, orders) {
    if (!orders.skills.length) return '<p class="muted">Enter skill attempts on the tribe.</p>';
    const entries = Object.entries(plan.skill_info).sort((a, b) => a[1].skill.localeCompare(b[1].skill));
    const details = skillDetails(u, plan, orders);
    return orders.skills.map((name, i) => {
      const detail = details[i];
      const label = `Attempt ${i + 1}${i === 2 ? " (teacher)" : ""}`;
      return `<label class="pl-field" for="plSkill${i}">${label}
        <select id="plSkill${i}" data-pl-skill="${i}"><option value="">None</option>
        ${entries.map(([code, s]) => {
          const selected = name === s.skill || name === code;
          return `<option value="${esc(s.skill)}"${selected ? " selected" : ""}>${esc(s.skill)} · Group ${esc(s.group)}</option>`;
        }).join("")}</select></label>
        ${detail ? `<div class="pl-skill-details"><strong>Group ${esc(detail.group)} · Level ${detail.level} · ${fmt(detail.chance)}% chance</strong>
          ${detail.cost ? `<small>Teacher costs ${fmt(detail.cost)} silver · half base chance</small>` : ""}
          ${i === 1 ? `<small>${detail.sameGroup ? "Quarter base chance · same group as attempt 1" : "Half base chance"}</small>` : ""}
          ${detail.problems.map((problem) => `<small class="pl-flag">${esc(problem)}</small>`).join("")}</div>` : ""}`;
    }).join("");
  }

  function gmRows(orders) {
    return Object.entries({ gm: "GM action", comments: "Comment", research: "Research attempt" }).map(([field, label]) =>
      `<div class="pl-phase">${orders[field].map((text, i) => `<div class="pl-gm-row">
        <label class="pl-field">${label} ${i + 1}<textarea data-pl-text="${field}" data-pl-line="${i}" rows="3">${esc(text)}</textarea></label>
        <button type="button" data-pl-gm-remove="${i}" data-pl-text="${field}" aria-label="Remove ${label} ${i + 1}">×</button>
        </div>`).join("")}
        <button type="button" data-pl-action="gm-add" data-pl-text="${field}">+ Add ${label}</button></div>`).join("");
  }

  function draftChecks(u, plan, orders = draft(u, plan), t = totals(u, plan, orders)) {
    const checks = [];
    const add = (section, text) => checks.push({ section, text });
    if (!u.new && t.free < 0) add("Activities", `${fmt(-t.free)} workers over budget.`);
    if ((!u.new || u.creation) && t.shortScouts) add("Activities", `Scouting needs ${fmt(t.shortScouts)} more warriors after BM transfers.`);
    if (t.parties > 8) add("Activities", `${t.parties} scout parties across the tribe and its elements. The limit is 8.`);
    const herdShortfall = u.herders.total - t.herders;
    if (herdShortfall > 0) add("Activities", `Assign ${fmt(herdShortfall)} more herders to cover the starting herd.`);
    const wagons = wagonCount(orders);
    const logs = u.inventory["Raw Materials"]?.Log || 0;
    if (wagons * 6 > logs) add("Activities", `${fmt(wagons)} wagons need ${fmt(wagons * 6)} logs. Only ${fmt(logs)} are on hand at the start.`);
    if (orders.rows.some(([name]) => name === "Woodwork · Clubs") && u.where?.terrain && !forest.has(u.where.terrain)) {
      add("Activities", "Clubs using standing timber need a wooded start hex.");
    }
    if (u.plan.move && t.margin < 0) add("Transfers", `Estimated movement load is ${pounds(-t.margin)} over reported capacity.`);
    if (t.afterMeal < 0) add("Transfers", `Estimated shortfall of ${fmt(-t.afterMeal)} provisions after eating. Check yields, transfers and the cap.`);
    const held = plan.held_scouts?.filter((route) => route.unit === u.unit).length || 0;
    if (held) add("Review", `${held} scout order(s) held after a movement change.`);
    const logistics = transferAnalysis(plan).get(u.unit);
    for (const issue of Object.values(logistics.issues)) add("Transfers", issue);
    for (const warning of logistics.warnings) add("Transfers", warning);
    for (const [i, detail] of skillDetails(u, plan, orders).entries()) {
      for (const problem of detail?.problems || []) add("Skills", `Attempt ${i + 1}. ${problem}`);
    }
    return checks;
  }

  function checks(u, plan) {
    const entries = draftChecks(u, plan);
    return entries.length ? `<ul class="pl-draft-checks">${entries.map(({ section, text }) =>
      `<li><button type="button" class="pl-inline" data-pl-section="${section}">${section}</button> · ${esc(text)}</li>`).join("")}</ul>`
      : '<p class="muted">No issues found in the current draft checks.</p>';
  }

  function review(plan) {
    const data = reviews.get(plan);
    const exportPanel = data ? `
      <div class="pl-export">
        <button type="button" data-pl-action="export" ${data.issues.length || !data.sheets.length ? "disabled" : ""}>Download paste-in workbook</button>
        <button type="button" data-pl-action="review-refresh">Refresh export preview</button>
        <p id="plExportStatus" role="status" hidden></p>
        <p class="muted">${data.exported_at ? (data.changed ? "Draft differs from last export." : "Draft matches last export.") : "Paste the listed ranges into the official orders workbook."}</p>
        ${data.issues.map((issue) => `<p class="pl-flag"><button type="button" class="pl-inline" data-pl-fix-unit="${esc(issue.unit)}"
          data-pl-section="${esc(issue.section)}" data-pl-fix-row="${issue.row}">${esc(issue.unit)} · ${esc(issue.section)} · row ${issue.row}</button> ${esc(issue.message)}</p>`).join("")}
        ${data.sheets.map((sheet) => `<details class="pl-export-sheet"><summary>${esc(sheet.name)} · ${sheet.rows.length} rows · paste at ${esc(sheet.paste_at)}</summary>
          <div class="pl-order-table"><table><thead><tr>${sheet.headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr></thead>
            <tbody>${sheet.rows.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`).join("")}
      </div>` : '<p class="muted" id="plReviewMessage">Loading export rows…</p>';
    return exportPanel + heldScouts(plan) + plan.units.map((u) => {
      const orders = draft(u, plan);
      const t = totals(u, plan, orders);
      return `<section class="pl-review-unit">
        <h4>${esc(u.unit)} ${esc(u.name)}</h4>
        <p>${fmt(t.assigned)} workers assigned · ${u.plan.scouts.length} scout parties
          ${u.plan.move ? ` · ${esc(u.plan.move.orders)} → ${esc(u.plan.move.end || "?")}` : ""}</p>
        ${draftChecks(u, plan, orders, t).map(({ text }) => `<p class="pl-flag">${esc(text)}</p>`).join("")}
        <details><summary>Order details</summary><ul>
          ${orders.rows.map(([activity, workers]) => `<li>${esc(activity)} · ${fmt(workers)} workers</li>`).join("")}
          ${orders.cap != null ? `<li>Keep up to ${fmt(orders.cap)} provisions before movement</li>` : ""}
          ${orders.transfers.map((order) => `<li>${PlannerTransfers.phases[order.phase]} · ${order.external === "receive" ? "Receive" : "Send"} ${fmt(order.qty)} ${esc(order.good)} ${order.external === "receive" ? "from" : "to"} ${esc(order.to || "unselected unit")}</li>`).join("")}
          ${orders.skills.map((name, i) => name ? `<li>Skill attempt ${i + 1} · ${esc(name)}</li>` : "").join("")}
          ${orders.gm.filter((text) => text.trim()).map((text) => `<li>GM Action · ${esc(text)}</li>`).join("")}
        </ul></details>
      </section>`;
    }).join("");
  }

  function heldScouts(plan) {
    if (!plan.held_scouts?.length) return "";
    return `<div class="pl-phase" id="plHeldScouts"><h4>Scouts held after movement changes</h4>
      ${plan.held_scouts.map((r) => `<p>${esc(r.unit)} · Scout ${r.party} · ${esc(r.orders)}
        <button type="button" data-pl-action="scout-restore" data-pl-unit="${esc(r.unit)}" data-pl-party="${r.party}">Use from new launch hex</button>
        <button type="button" data-pl-action="scout-discard" data-pl-unit="${esc(r.unit)}" data-pl-party="${r.party}">Omit order</button></p>`).join("")}</div>`;
  }

  function carryForward(u, plan, orders) {
    const prior = plan.previous_export;
    const from = prior?.worksheet[u.unit];
    if (!from) return "";
    const labels = { rows: "Activities", transfers: "Transfers", skills: "Skills", gm: "GM Actions",
      comments: "Comments", research: "Research" };
    const options = Object.entries(labels).filter(([field]) => from[field]?.some(Boolean) && !orders[field].some(Boolean));
    if (!options.length) return "";
    return `<details class="pl-phase pl-carry"><summary>Bring forward from ${esc(prior.turn)} · ${esc(prior.variant)}</summary>
      <form id="plCarryForward" class="pl-editor-actions">${options.map(([field, label]) =>
        `<label><input type="checkbox" name="section" value="${field}"> ${label}</label>`).join("")}
      <button type="submit">Copy selected orders</button></form></details>`;
  }

  async function resolveScout(button, plan, restore) {
    button.disabled = true;
    try {
      await api(endpoint(plan, "scout-review"), {
        json: { unit: button.dataset.plUnit, party: Number(button.dataset.plParty), restore },
      });
      await loadAll(true);
    } catch (err) {
      const message = document.createElement("span");
      message.className = "pl-flag";
      message.textContent = err.message;
      button.after(message);
    } finally {
      button.disabled = false;
    }
  }

  async function loadReview(plan) {
    try {
      await flush(plan);
      const result = await api(endpoint(plan, "review"));
      reviews.set(plan, result);
      if (state.planner === plan && view === "Review") renderPlanner();
    } catch (err) {
      const message = document.getElementById("plReviewMessage");
      if (state.planner === plan && message) message.textContent = err.message;
    }
  }

  async function exportDraft(button, plan, message, fresh = false) {
    button.disabled = true;
    message.hidden = true;
    message.classList.remove("pl-flag");
    try {
      await flush(plan);
      const review = fresh ? await api(endpoint(plan, "review")) : reviews.get(plan);
      if (!review) throw new Error("Refresh Review to load the export rows.");
      if (review.issues.length || !review.sheets.length) {
        if (!review.sheets.length && !review.issues.length) throw new Error("This plan has no orders to export.");
        if (state.tab === "planner" && state.planner === plan) {
          reviews.set(plan, review);
          view = "Review";
          renderPlanner();
        }
        throw new Error("Resolve the incomplete orders in Review before exporting.");
      }
      const response = await fetch(endpoint(plan, "export"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ digest: review.digest, estimates: Object.fromEntries(plan.units.map((unit) => {
          const result = transferAnalysis(plan).get(unit.unit);
          const stock = result.finalStock;
          const provisions = totals(unit, plan).afterMeal;
          return [unit.unit, { ...(provisions == null ? {} : { Provs: Math.max(0, provisions) }),
            ...(stock.has("HORSE") ? { Horse: stock.get("HORSE").qty } : {}),
            ...(stock.has("LOG") ? { Log: stock.get("LOG").qty } : {}) }];
        })) }),
      });
      const filename = await download(response, "PlanPaste.xlsx");
      message.textContent = `Downloaded ${filename}`;
      message.hidden = false;
      await loadReview(plan);
    } catch (err) {
      message.classList.add("pl-flag");
      message.textContent = err.message;
      message.hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  function loaded(plan) {
    status();
    if (view === "Review") loadReview(plan);
  }

  function editor(u, plan) {
    const orders = draft(u, plan);
    const bodies = {
      Activities: () => activityRows(u, plan, orders),
      Transfers: () => PlannerTransfers.editor(u, plan, orders, transferAnalysis(plan).get(u.unit)),
      Skills: () => skillRows(u, plan, orders),
      "GM Actions": () => gmRows(orders),
      Review: () => review(plan),
    };
    return `<div class="pl-order-heading"><h3>Draft orders</h3><small class="muted" id="plSaveStatus" role="status">Draft saved with plan</small></div>
      <nav class="pl-order-nav" aria-label="Draft order sections">${sections.map((section) =>
        `<button type="button" data-pl-section="${section}" aria-current="${view === section}">${section}</button>`).join("")}</nav>
      <div class="pl-order-body">${bodies[view]()}${view === "Review" ? "" : carryForward(u, plan, orders)}</div>`;
  }

  function refreshBudget(u, plan) {
    document.getElementById("plBudget").innerHTML = budget(u, plan);
    document.getElementById("plDraftChecks").innerHTML = checks(u, plan);
    const cell = document.querySelector(`[data-pl-worker-balance="${CSS.escape(u.unit)}"]`);
    cell.textContent = balance(u, plan);
    for (const unit of plan.units) {
      document.querySelector(`[data-pl-draft-count="${CSS.escape(unit.unit)}"]`).innerHTML = checkCount(unit, plan);
    }
    applyPlannerFilter();
    const orders = draft(u, plan);
    const remainder = document.getElementById("plWorkRemaining");
    if (remainder) {
      const total = totals(u, plan, orders);
      remainder.innerHTML = workerRemainder(u, total);
      for (const button of document.querySelectorAll("[data-pl-worker-row]")) {
        const action = button.dataset.plAction;
        if (action === "worker-minus") button.disabled = !orders.rows[button.dataset.plWorkerRow][1];
        if (action === "worker-half") button.disabled = total.free < 2;
        if (action === "worker-max") button.disabled = total.free < 1;
      }
    }
    for (const detail of document.querySelectorAll("[data-pl-output-row]")) {
      const index = detail.dataset.plOutputRow;
      const row = orders.rows[index];
      const { source } = activityOutputs(row, u, plan);
      detail.querySelector("[data-pl-output-total]").textContent = outputSummary(row, u, plan);
      detail.querySelector("[data-pl-output-source]").textContent = source;
      detail.querySelector('[data-pl-action="output-reset"]').hidden = source !== "Manual estimate" || !observedOutputs(row, u, plan);
    }
    PlannerTransfers.refreshRows(plan, orders, transferAnalysis(plan).get(u.unit));
  }

  function checkCount(u, plan) {
    const count = draftChecks(u, plan).length;
    return `<span class="count${count ? " warn" : ""}">${count}</span>`;
  }

  function refreshEditor(u, plan) {
    const card = document.querySelector(".pl-editor");
    card.innerHTML = editor(u, plan);
    refreshBudget(u, plan);
    return card;
  }

  function activityFields(row, plan) {
    row[3] ??= {};
    const fields = row[3];
    const codes = activityCatalog(plan).get(row[0]);
    if (codes) for (const key of ["activity", "item", "distinction"]) fields[key] ??= codes[key];
    fields.implements ??= [];
    fields.materials ??= [];
    return fields;
  }

  function manualOutputs(row, u, plan) {
    const outputs = structuredClone(activityOutputs(row, u, plan).outputs);
    const fields = activityFields(row, plan);
    fields.outputs = outputs;
    row[2] = null;
    return outputs;
  }

  function addActivity(u, plan, name) {
    const orders = draft(u, plan);
    const match = [...activityCatalog(plan).keys()].find((option) => option.toLowerCase() === name.toLowerCase());
    const row = [match || name, 0, null];
    activityFields(row, plan);
    orders.rows.push(row);
    saveDraft(u, plan, orders);
    refreshEditor(u, plan).querySelector(`[data-pl-workers="${orders.rows.length - 1}"]`).focus();
  }

  function click(event, plan, unit) {
    const button = event.target.closest("button");
    if (!button || !plan) return false;
    const { plSection, plAction, plRemove, plSuggest, plGmRemove, plTransferAdd, plTransferRemove, plFixUnit, plFixRow } = button.dataset;
    if (!plSection && !plAction && !plSuggest && !plTransferAdd &&
        plRemove === undefined && plGmRemove === undefined && plTransferRemove === undefined) return false;
    const u = plan.units.find((candidate) => candidate.unit === unit);
    if (!u) return false;
    if (plAction === "export") { exportDraft(button, plan, document.getElementById("plExportStatus")); return true; }
    if (plAction === "review-refresh") { loadReview(plan); return true; }
    if (plAction === "scout-restore" || plAction === "scout-discard") {
      resolveScout(button, plan, plAction === "scout-restore");
      return true;
    }
    if (plFixUnit) {
      if (plSection === "Scouts") {
        document.getElementById("plHeldScouts")?.scrollIntoView({ block: "center" });
        return true;
      }
      view = plSection;
      state.plUnit = plFixUnit;
      renderPlanner();
      if (plSection === "Activities") {
        const field = document.querySelector(`[data-pl-workers="${Number(plFixRow) - 1}"]`);
        const details = field?.closest("tr")?.querySelector("details");
        if (details) details.open = true;
        field?.focus();
      } else {
        document.querySelector(`[data-transfer-row="${Number(plFixRow) - 1}"] [data-transfer-field="to"]`)?.focus();
      }
      return true;
    }
    const orders = draft(u, plan);
    if (plAction?.startsWith("worker-")) {
      const index = button.dataset.plWorkerRow;
      const remaining = Math.max(0, totals(u, plan, orders).free);
      const deltas = { "worker-minus": -1, "worker-plus": 1, "worker-half": Math.floor(remaining / 2), "worker-max": remaining };
      const row = orders.rows[index];
      row[1] = Math.min(999999999999999, Math.max(0, row[1] + deltas[plAction]));
      document.querySelector(`[data-pl-workers="${index}"]`).value = row[1];
      saveDraft(u, plan, orders);
      refreshBudget(u, plan);
      return true;
    }
    if (plSection) view = plSection;
    if (plSuggest) {
      addActivity(u, plan, plSuggest);
      return true;
    }
    if (plAction === "hunt") {
      const row = orders.rows.find(([name]) => name === "Hunting");
      row[1] = Math.max(0, row[1] + totals(u, plan, orders).free);
    }
    if (plRemove !== undefined) orders.rows.splice(Number(plRemove), 1);
    if (plGmRemove !== undefined) orders[button.dataset.plText].splice(Number(plGmRemove), 1);
    if (plAction === "gm-add") orders[button.dataset.plText].push("");
    const fieldIndex = button.dataset.plActivityIndex;
    if (plAction?.startsWith("output-")) {
      const row = orders.rows[fieldIndex];
      if (plAction === "output-reset") {
        delete activityFields(row, plan).outputs;
        row[2] = null;
      } else {
        const outputs = manualOutputs(row, u, plan);
        if (plAction === "output-add" && outputs.length < 20) {
          const item = (row[3]?.item || activityCatalog(plan).get(row[0])?.item || "").toUpperCase();
          outputs.push([!outputs.length && plan.goods_catalog?.[item] ? item : "", 0, "total"]);
        }
        if (plAction === "output-remove") outputs.splice(Number(button.dataset.plOutputIndex), 1);
      }
    }
    if (plAction === "pair-add" || plAction === "pair-remove") {
      const pairs = activityFields(orders.rows[fieldIndex], plan)[button.dataset.plList];
      if (plAction === "pair-add" && pairs.length < 20) pairs.push(["", 0]);
      if (plAction === "pair-remove") pairs.splice(Number(button.dataset.plPairIndex), 1);
    }
    if (plTransferAdd) {
      const recipients = Object.keys(u.transfer_neighbours?.[plTransferAdd] || {});
      const external = button.dataset.plExternal || null;
      orders.transfers.push({ phase: plTransferAdd, to: !external && recipients.length === 1 ? recipients[0] : "",
        good: "", qty: 0, confirmed: "", external, reserve: !external && recipients.length === 1 &&
          Boolean(plan.units.find((other) => other.unit === recipients[0])?.creation) });
    }
    if (plTransferRemove !== undefined) orders.transfers.splice(Number(plTransferRemove), 1);
    if (!plSection) saveDraft(u, plan, orders);
    const card = refreshEditor(u, plan);
    if (plSection) card.querySelector('[aria-current="true"]').focus({ preventScroll: true });
    if (plAction === "gm-add") card.querySelector(`textarea[data-pl-text="${button.dataset.plText}"][data-pl-line="${orders[button.dataset.plText].length - 1}"]`).focus();
    if (plAction?.startsWith("output-")) {
      const detail = card.querySelector(`[data-pl-output-row="${fieldIndex}"]`);
      detail.open = true;
      if (plAction === "output-add") [...detail.querySelectorAll('[data-pl-output-field="0"]')].at(-1)?.focus();
    } else if (fieldIndex !== undefined) {
      card.querySelector(`[data-pl-activity-index="${fieldIndex}"]`).closest("details").open = true;
    }
    if (plTransferAdd) {
      const row = card.querySelector(`[data-transfer-row="${orders.transfers.length - 1}"]`);
      const to = row.querySelector('[data-transfer-field="to"]');
      (to.tagName === "SELECT" && to.options.length === 1 ? row.querySelector('[data-transfer-field="good"]') : to).focus();
    }
    if (plSection === "Review") loadReview(plan);
    return true;
  }

  function submit(event, plan, unit) {
    if (event.target.id === "plCarryForward") {
      event.preventDefault();
      const u = plan?.units.find((candidate) => candidate.unit === unit);
      if (!u) return;
      const from = plan.previous_export?.worksheet[unit];
      const orders = draft(u, plan);
      for (const field of new FormData(event.target).getAll("section")) {
        if (!from?.[field] || orders[field].some(Boolean)) continue;
        orders[field] = structuredClone(from[field]);
        if (field === "transfers") for (const transfer of orders[field]) transfer.confirmed = "";
      }
      saveDraft(u, plan, orders);
      refreshEditor(u, plan);
      return;
    }
    if (event.target.id !== "plActivityForm") return;
    event.preventDefault();
    const u = plan?.units.find((candidate) => candidate.unit === unit);
    const name = document.getElementById("plAddActivity").value.trim();
    if (u && name) addActivity(u, plan, name);
  }

  function input({ target }, plan, unit) {
    if (!plan || !target.closest(".pl-editor") || !target.validity?.valid) return;
    const u = plan.units.find((candidate) => candidate.unit === unit);
    if (!u) return;
    const orders = draft(u, plan);
    const { plWorkers, plSkill, plText, plLine, plActivityIndex, plActivityField, plPairCode, plPairQty, plList,
      plOutputField, plOutputIndex } = target.dataset;
    const transferField = target.dataset.transferField;
    if (transferField) {
      const index = target.closest("[data-transfer-row]").dataset.transferRow;
      const order = orders.transfers[index];
      order[transferField] = transferField === "qty" ? target.valueAsNumber || 0 :
        transferField === "reserve" ? target.checked :
        transferField === "confirmed" ? (target.checked ? PlannerTransfers.confirmationKey(u, order) : "") : target.value;
      if (transferField === "to") {
        order.confirmed = "";
        order.reserve = Boolean(plan.units.find((candidate) => candidate.unit === order.to)?.creation);
      }
      saveDraft(u, plan, orders);
      if (transferField === "qty" || (transferField === "to" && order.external)) refreshBudget(u, plan);
      else if (transferField === "notes") return;
      else refreshEditor(u, plan).querySelector(`[data-transfer-row="${index}"] [data-transfer-field="${transferField}"]`).focus();
      return;
    }
    if (plOutputField !== undefined) {
      const outputs = manualOutputs(orders.rows[plActivityIndex], u, plan);
      outputs[plOutputIndex][plOutputField] = plOutputField === "1" ? target.valueAsNumber || 0 : target.value;
    } else if (plActivityIndex !== undefined) {
      const fields = activityFields(orders.rows[plActivityIndex], plan);
      if (plActivityField) fields[plActivityField] = target.value;
      else if (plPairCode !== undefined) fields[plList][plPairCode][0] = target.value;
      else if (plPairQty !== undefined) fields[plList][plPairQty][1] = target.valueAsNumber || 0;
    } else if (plLine !== undefined) orders[plText][Number(plLine)] = target.value;
    else if (plSkill !== undefined) orders.skills[Number(plSkill)] = target.value;
    else if (target.id === "plProvisionCap") orders.cap = target.value === "" ? null : target.valueAsNumber;
    else if (plWorkers !== undefined) orders.rows[Number(plWorkers)][1] = target.valueAsNumber || 0;
    else return;
    saveDraft(u, plan, orders);
    if (target.type === "number" || plOutputField !== undefined || plActivityField !== undefined) refreshBudget(u, plan);
    if (plSkill !== undefined) refreshEditor(u, plan).querySelector(`[data-pl-skill="${plSkill}"]`).focus();
  }

  return { balance, budget, checks, checkCount, editor, click, input, submit, flush, loaded, status, exportDraft };
})();

const PlannerTransfers = (() => {
  const phases = { BM: "Before movement", AM: "After movement" };
  const provisions = "PROVS";
  const people = new Set(["WARRIORS", "ACTIVES", "INACTIVES", "SLAVE", "SLAVES"]);

  function inventory(u) {
    const held = new Map();
    for (const [section, stock] of Object.entries({ ...u.inventory, People: u.people })) {
      for (const [label, qty] of Object.entries(stock)) {
        const good = label.toUpperCase();
        if (section !== "People" || people.has(good)) held.set(good, { label, qty, section });
      }
    }
    return held;
  }

  function change(stock, good, item, amount) {
    stock.set(good, { ...item, qty: (stock.get(good)?.qty || 0) + amount });
  }

  function confirmationKey(u, order) {
    const option = u.transfer_neighbours?.[order.phase]?.[order.to];
    return option ? [order.phase, u.transfer_hexes[order.phase], option.hex, ...option.requirements].join("|") : "";
  }

  function analyse(plan, orders, production) {
    const units = new Map(plan.units.map((u) => [u.unit, u]));
    const weights = new Map(Object.entries(plan.goods_info).map(([label, details]) => [label.toUpperCase(), details.weight]));
    const catalogue = plan.goods_catalog || {};
    const stock = new Map(plan.units.map((u) => [u.unit, inventory(u)]));
    const result = new Map(plan.units.map((u) => [u.unit, {
      stock: {}, incoming: {}, issues: {}, warnings: [], before: 0, carried: 0, finalProvs: 0, peopleDelta: 0, weightDelta: 0,
      reservedWarriors: 0, reservedActives: 0, movingWarriors: 0, finalStock: null,
    }]));
    const nextTurnGoods = new Map();
    for (const u of plan.units) {
      const held = stock.get(u.unit);
      const { yields, materials } = production(orders.get(u.unit), u);
      change(held, provisions, { label: "Provs", section: "Finished Goods" }, 0);
      for (const [name, qty] of materials) {
        const code = name.trim().toUpperCase();
        if (!qty || !code) continue;
        const good = catalogue[code]?.short.toUpperCase() || code;
        const item = held.get(good);
        const available = item?.qty || 0;
        if (qty > available) result.get(u.unit).warnings.push(`${code}: activities reserve ${num(qty)} with ${num(available)} available.`);
        if (available) change(held, good, item, -Math.min(available, qty));
      }
      const deferred = new Map();
      for (const output of yields) {
        if (!output.code || !output.qty) continue;
        const kind = catalogue[output.code];
        if (!kind || kind.category === "HUMANS") {
          result.get(u.unit).warnings.push(`${output.code}: choose a listed good for this production estimate.`);
          continue;
        }
        const good = kind.short.toUpperCase();
        const item = held.get(good) || { label: output.name, section: kind.category };
        change(output.deferred ? deferred : held, good, item, output.qty);
      }
      nextTurnGoods.set(u.unit, deferred);
      result.get(u.unit).before = held.get(provisions).qty;
    }

    for (const phase of Object.keys(phases)) {
      const receipts = [];
      for (const u of plan.units) {
        const held = stock.get(u.unit);
        const summary = result.get(u.unit);
        summary.stock[phase] = new Map(held);
        summary.incoming[phase] = [];
      }
      for (const u of plan.units) {
        const held = stock.get(u.unit);
        const summary = result.get(u.unit);
        for (const [i, order] of orders.get(u.unit).transfers.entries()) {
          if (order.phase !== phase) continue;
          const target = units.get(order.to);
          const option = u.transfer_neighbours?.[phase]?.[order.to];
          const item = held.get(order.good);
          const sharesStock = target && (u.goods_tribe === target.unit || target.goods_tribe === u.unit ||
            (u.goods_tribe && u.goods_tribe === target.goods_tribe));
          let issue = "";
          if (!order.good || !order.qty || (order.external ? !/^\d{4}(?:[a-z]\d+)?$/.test(order.to) : !target)) {
            issue = "Choose a unit, a good and a positive quantity.";
          } else if (order.external && target) {
            issue = "Use a clan transfer for this unit.";
          } else if (order.external === "receive") {
            const good = catalogue[order.good];
            if (good) {
              receipts.push({ ...order, to: u.unit, from: order.to, good: good.short.toUpperCase(),
                item: { label: good.short, section: good.category } });
              continue;
            }
            issue = "Select a good from the orders list.";
          } else if (order.external === "send") {
            if (order.qty > (item?.qty || 0)) issue = `Only ${num(item?.qty || 0)} available for this external order.`;
            else {
              change(held, order.good, item, -order.qty);
              continue;
            }
          }
          else if (!option) issue = "These units are outside transfer range at this timing.";
          else if (option.requirements.length && order.confirmed !== confirmationKey(u, order)) issue = `Confirm ${option.requirements.join(" and ")}.`;
          else if (sharesStock && !people.has(order.good)) issue = "Goods Tribe stock is shared. A goods transfer between these units has no effect.";
          else if (["Structures", "Buildings"].includes(item?.section) && !option.kind.startsWith("Same hex")) {
            issue = "Buildings stay in their hex and cannot be transferred to an adjacent hex.";
          } else if (order.qty > (item?.qty || 0)) {
            issue = `Only ${num(item?.qty || 0)} ${item?.label || order.good} available at this point in the draft.`;
          }
          if (issue) {
            summary.issues[i] = issue;
            continue;
          }
          change(held, order.good, item, -order.qty);
          if (phase === "BM" && order.reserve && order.good === "WARRIORS") summary.reservedWarriors += order.qty;
          if (phase === "BM" && order.reserve && order.good === "ACTIVES") summary.reservedActives += order.qty;
          receipts.push({ ...order, item, from: u.unit });
        }
      }
      for (const receipt of receipts) {
        change(stock.get(receipt.to), receipt.good, receipt.item, receipt.qty);
        result.get(receipt.to).incoming[phase].push(receipt);
      }
      for (const u of plan.units) {
        const held = stock.get(u.unit);
        const summary = result.get(u.unit);
        if (phase === "BM") {
          const item = held.get(provisions);
          const cap = orders.get(u.unit).cap;
          if (cap != null) change(held, provisions, item, Math.min(0, cap - item.qty));
          summary.carried = held.get(provisions).qty;
          summary.movingWarriors = held.get("WARRIORS")?.qty || 0;
          const start = inventory(u);
          const deferred = nextTurnGoods.get(u.unit);
          for (const good of new Set([...held.keys(), ...deferred.keys()])) {
            const item = held.get(good) || deferred.get(good);
            const delta = (held.get(good)?.qty || 0) + (deferred.get(good)?.qty || 0) - (start.get(good)?.qty || 0);
            if (!delta || people.has(good)) continue;
            const weight = good === provisions ? 10 : weights.get(good) ?? catalogue[good]?.weight;
            if (weight == null) summary.warnings.push(`${item.label} has an unknown weight and is omitted from the load estimate.`);
            else summary.weightDelta += delta * weight;
          }
        } else {
          for (const [good, output] of nextTurnGoods.get(u.unit)) change(held, good, output, output.qty);
          summary.finalProvs = held.get(provisions).qty;
          summary.peopleDelta = sumPeople(held) - sumPeople(inventory(u));
          summary.finalStock = held;
        }
      }
    }
    return result;
  }

  function sumPeople(stock) {
    return [...people].reduce((total, good) => total + (stock.get(good)?.qty || 0), 0);
  }

  const unitLabel = (u) => u ? [u.unit, u.name].filter(Boolean).join(" · ") : "";
  const choice = (value, label, selected) =>
    `<option value="${esc(value)}"${value === selected ? " selected" : ""}>${esc(label)}</option>`;

  function goodsChoices(stock, selected) {
    const available = [...stock].filter(([, item]) => item.qty > 0).sort((a, b) => a[1].label.localeCompare(b[1].label));
    const choices = available.map(([good, item]) => choice(good, `${item.label} · ${num(item.qty)}`, selected));
    if (selected && !available.some(([good]) => good === selected)) choices.push(choice(selected, `${selected} · unavailable`, selected));
    return choice("", "Select a good", selected) + choices.join("");
  }

  function orderGoods(plan, summary, order) {
    if (order.external !== "receive") return goodsChoices(summary.stock[order.phase], order.good);
    const choices = Object.keys(plan.goods_catalog || {}).sort().map((good) => choice(good, good, order.good));
    return choice("", "Select a good", order.good) + choices.join("");
  }

  function phaseRows(u, plan, orders, summary, phase) {
    const options = Object.entries(u.transfer_neighbours?.[phase] || {}).sort((a, b) =>
      a[1].requirements.length - b[1].requirements.length || a[0].localeCompare(b[0]));
    const rows = orders.transfers.map((order, i) => {
      if (order.phase !== phase) return "";
      const recipient = options.find(([id]) => id === order.to)?.[1];
      const rowLabel = `${phases[phase]} transfer ${1 + orders.transfers.slice(0, i).filter((r) => r.phase === phase).length}`;
      return `<div class="pl-transfer-row" data-transfer-row="${i}">
        ${order.external ? `<label>${order.external === "receive" ? "Receive from" : "Send to"} (external)
          <input type="text" maxlength="9" value="${esc(order.to)}" data-transfer-field="to" aria-label="${rowLabel} external unit"></label>` :
          `<label>To<select data-transfer-field="to" aria-label="${rowLabel} recipient">${choice("", options.length ? "Select a unit" : "No clan recipient in range", order.to)}
          ${options.map(([id, option]) => choice(id,
            `${unitLabel(plan.units.find((unit) => unit.unit === id))} — ${option.hex} · ${option.kind}${option.requirements.length ? " (check requirements)" : ""}`, order.to)).join("")}
          ${order.to && !recipient ? choice(order.to, `${order.to} · outside range or unknown`, order.to) : ""}
        </select></label>`}
        <label>Good<select data-transfer-field="good" aria-label="${rowLabel} good">${orderGoods(plan, summary, order)}</select></label>
        <label>Quantity<input class="pl-transfer-quantity" type="number" min="1" max="999999999999999" step="1"
          data-transfer-field="qty" value="${order.qty || ""}" aria-label="${rowLabel} quantity"></label>
        <button type="button" data-pl-transfer-remove="${i}" aria-label="Remove ${rowLabel}">×</button>
        <label class="pl-transfer-confirm">Notes <input type="text" data-transfer-field="notes" value="${esc(order.notes)}" aria-label="${rowLabel} notes"></label>
        ${recipient ? `<small class="muted">${esc(recipient.hex)} · ${esc(recipient.kind)}</small>` : ""}
        ${phase === "BM" && ["WARRIORS", "ACTIVES"].includes(order.good) ? `<label class="pl-transfer-confirm">
          <input type="checkbox" data-transfer-field="reserve"${order.reserve ? " checked" : ""}>Reserve these workers from activities</label>` : ""}
        ${recipient?.requirements.length ? `<label class="pl-transfer-confirm"><input type="checkbox" data-transfer-field="confirmed"${order.confirmed === confirmationKey(u, order) ? " checked" : ""}>
          Confirm ${esc(recipient.requirements.join(" and "))}</label>` : ""}
        <small class="pl-flag" data-transfer-issue>${esc(summary.issues[i])}</small>
      </div>`;
    }).join("");
    return `<section class="pl-transfer-phase">
      <h4>${phases[phase]} <span class="muted">· ${esc(u.transfer_hexes?.[phase] || "hex unknown")}</span></h4>
      ${phase === "AM" && options.length ? '<small class="muted">Recipients use planned destinations. Actual movement may differ.</small>' : ""}
      ${rows}
      <button type="button" data-pl-transfer-add="${phase}">+ Add ${phase} transfer</button>
      <details class="pl-external-transfer"><summary>External orders</summary>
        <button type="button" data-pl-transfer-add="${phase}" data-pl-external="send">Send to external unit</button>
        <button type="button" data-pl-transfer-add="${phase}" data-pl-external="receive">Receive from external unit</button>
        <small class="muted">Use the published unit code and check the event or trading rules.</small></details>
      ${options.length ? "" : `<small class="muted">No clan recipient in range ${phase === "BM" ? "at the starting hexes" : "at the planned destinations"}. Use External orders for a published unit code.</small>`}
      ${summary.incoming[phase].length ? `<ul class="pl-transfer-incoming">${summary.incoming[phase].map((r) =>
        `<li>Incoming · ${num(r.qty)} ${esc(r.item.label)} from ${esc(r.from)}</li>`).join("")}</ul>` : ""}
    </section>`;
  }

  function editor(u, plan, orders, summary) {
    return `${u.goods_tribe && u.goods_tribe !== u.unit ? `<p class="muted">Reported Goods Tribe ${esc(u.goods_tribe)}.
      Shared goods are held by the Goods Tribe.</p>` : ""}
      <p class="muted pl-transfer-stock-note">Estimates include production, material commitments and transfers. Fresh Forestry logs enter next turn’s stock.</p>
      ${Object.keys(phases).map((phase) => phaseRows(u, plan, orders, summary, phase)).join("")}
      <div class="pl-transfer-phase">
        <label class="pl-provision-cap">Keep up to <input type="number" min="0" max="999999999999999" step="1"
          class="pl-transfer-quantity" id="plProvisionCap" placeholder="No cap" value="${orders.cap ?? ""}"
          aria-label="Provision cap"> provisions before moving</label>
        <small class="muted">The estimate applies this cap after BM transfers.</small>
      </div>`;
  }

  function refreshRows(plan, orders, summary) {
    for (const row of document.querySelectorAll("[data-transfer-row]")) {
      const index = row.dataset.transferRow;
      const order = orders.transfers[index];
      row.querySelector("[data-transfer-issue]").textContent = summary.issues[index] || "";
      const picker = row.querySelector('[data-transfer-field="good"]');
      const choices = orderGoods(plan, summary, order);
      if (picker.innerHTML !== choices) picker.innerHTML = choices;
    }
  }

  return { analyse, confirmationKey, editor, phases, refreshRows };
})();

const API_BASE = (window.BLOODNET_API_BASE_URL || "").replace(/\/$/, "");
const state = { caseView: null, role: "bank_admin", patientDraft: null, unsubscribeCase: null };

const $ = (selector) => document.querySelector(selector);
$("#current-date").textContent = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", year: "numeric" }).format(new Date()).toUpperCase();
const getAuthToken = () => {
    const sessionValue = window.sessionStorage.getItem("bloodnet_access_token");
    return window.BLOODNET_ACCESS_TOKEN || sessionValue || "";
};
const api = async (path, options = {}) => {
    const token = getAuthToken();
    const headers = { "Content-Type": "application/json", ...(options.headers || {}), Authorization: `Bearer ${token}` };
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `API request failed (${response.status})`);
    return body;
};

const livePayload = async (request) => {
    const context = await api("/match-svc/api/v1/hospital/context");
    return { request, ...context, donors: [], eligible_donor_ids: [] };
};

function showNotice(message, error = false) { const notice = $("#notice"); notice.textContent = message; notice.className = `notice ${error ? "error" : "success"}`; notice.hidden = false; }
function logActivity(label, detail) { const list = $("#activity-list"); if (list.querySelector(".muted")) list.innerHTML = ""; const item = document.createElement("div"); item.className = "activity-item"; item.innerHTML = `<span class="activity-dot"></span><div><strong>${label}</strong><span>${detail}</span></div><time>${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>`; list.prepend(item); }

function renderCase(view) {
    state.caseView = view; const { case: currentCase, recommendations = [], ranked_donors = [] } = view; const proposal = recommendations[0];
    $("#empty-state").hidden = true; $("#case-content").hidden = false; $("#case-id").textContent = currentCase.case_id; $("#case-title").textContent = `${currentCase.request_id} / ${currentCase.units_from_inventory + currentCase.units_from_donors_remaining} units requested`;
    const stateText = currentCase.reservation_state.replaceAll("_", " "); $("#case-state").textContent = stateText; $("#case-state").className = `state-pill ${currentCase.reservation_state}`;
    $("#inventory-coverage").textContent = currentCase.units_from_inventory; $("#case-shortfall").textContent = currentCase.units_from_donors_remaining; $("#donor-count").textContent = `${ranked_donors.length} ranked`;
    const swarm = view.swarm || {}; const notifications = view.notifications || []; const steps = ["Request", "Case created", "Inventory", "Donor coverage", "Reservation", "Approval", "Reserved", "Shortfall", "Swarm"]; const active = swarm.status === "initiated" || swarm.status === "inventory_covered" ? 8 : currentCase.reservation_state === "reserved" ? 6 : proposal ? 5 : 4; $("#flow-rail").innerHTML = steps.map((step, index) => `<div class="flow-step ${index <= active ? "complete" : ""} ${index === active ? "current" : ""}"><i>${index < active ? "&#10003;" : index + 1}</i><span>${step}</span></div>`).join("");
    $("#proposal-status").textContent = proposal ? proposal.state.replaceAll("_", " ") : "No inventory proposal"; $("#proposal-detail").innerHTML = proposal ? `<p><strong>${proposal.payload.unit_ids.length} unit${proposal.payload.unit_ids.length === 1 ? "" : "s"}</strong> from ${proposal.payload.bank_id}</p><span class="muted">${proposal.rec_id} &middot; inventory remains untouched until approval</span>` : `<p class="muted">No on-hand units selected. The case can proceed to donor mobilization.</p>`; $("#approval-actions").hidden = !proposal || proposal.state !== "AWAITING_APPROVAL";
    $("#donor-list").innerHTML = ranked_donors.length ? ranked_donors.slice(0, 5).map((donor) => `<div class="donor-row"><span>${donor.donor_id}</span><span>${donor.blood_group}</span><strong>${Math.round(donor.success_probability * 100)}%</strong></div>`).join("") : `<span class="muted">No compatible eligible donors returned.</span>`;
    $("#swarm-status").textContent = swarm.status ? swarm.status.replaceAll("_", " ") : "pending"; $("#swarm-detail").innerHTML = swarm.donors_contacted?.length ? `<p><strong>${swarm.cohort_size} donor${swarm.cohort_size === 1 ? "" : "s"}</strong> selected for round 1</p><span class="muted">${swarm.donors_contacted.join(" / ")}</span>` : `<span class="muted">${swarm.status === "inventory_covered" ? "Inventory covers the request; donor mobilization skipped." : "Waiting for reservation decision."}</span>`;
    $("#notification-count").textContent = `${notifications.length} sent`; $("#notification-detail").innerHTML = notifications.length ? notifications.map((notification) => `<div class="donor-row"><span>${notification.donor_id}</span><span>${notification.channel}</span><strong>${notification.status}</strong></div>`).join("") : `<span class="muted">No notifications sent.</span>`;
    updateMetrics([view]);
}

function subscribeToCase(caseId) {
    if (state.unsubscribeCase) state.unsubscribeCase();
    state.unsubscribeCase = window.bloodNetRealtime.subscribeToCase(caseId, (view) => {
        renderCase(view);
        if (state.role === "patient") renderPatientTracking(view);
    }, (error) => showNotice(error.message, true));
}

function updateMetrics(views) { $("#open-cases").textContent = views.length; $("#awaiting-approval").textContent = views.filter(({ case: item }) => item.reservation_state === "awaiting_approval").length; $("#reserved-units").textContent = views.reduce((sum, { case: item }) => sum + (item.reservation_state === "reserved" ? item.units_from_inventory : 0), 0); $("#donor-shortfall").textContent = views.reduce((sum, { case: item }) => sum + item.units_from_donors_remaining, 0); }
function renderPatientSummary(request) { $("#patient-summary").innerHTML = `<div><span>Blood group</span><strong>${request.group}</strong></div><div><span>Component</span><strong>${request.component}</strong></div><div><span>Quantity</span><strong>${request.qty} units</strong></div><div><span>Urgency</span><strong class="patient-urgency">${request.urgency}</strong></div><div><span>Hospital</span><strong>${request.hospital_id}</strong></div>`; }
function renderPatientTracking(view) { const currentCase = view.case; const request = view.request || state.patientDraft; const inventory = currentCase.units_from_inventory; const shortfall = currentCase.units_from_donors_remaining; $("#patient-tracking").hidden = false; $("#patient-case-heading").textContent = `Request ${currentCase.request_id}`; $("#patient-status").textContent = currentCase.reservation_state.replaceAll("_", " "); $("#patient-inventory").textContent = `${inventory} unit${inventory === 1 ? "" : "s"}`; $("#patient-shortfall").textContent = `${shortfall} unit${shortfall === 1 ? "" : "s"}`; $("#patient-fulfillment").textContent = currentCase.outcome === "fulfilled" ? "Fulfilled" : "In progress"; $("#patient-tracking-note").textContent = inventory ? `${inventory} unit${inventory === 1 ? " is" : "s are"} covered by nearby inventory. BloodNet is coordinating ${shortfall} additional donor unit${shortfall === 1 ? "" : "s"}.` : "BloodNet is searching for eligible donors and will keep this request updated."; if (request) renderPatientSummary(request); }
async function loadCases() { const { cases } = await api(`/match-svc/api/v1/cases?role=${state.role}`); updateMetrics(cases); if (cases.length) { const view = await api(`/match-svc/api/v1/cases/${cases[cases.length - 1].case.case_id}?role=${state.role}`); if (state.role === "patient") renderPatientTracking(view); else renderCase(view); subscribeToCase(view.case.case_id); } }

$("#request-form").addEventListener("submit", async (event) => { event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true; try { const intake = await api("/intake-svc/api/v1/extract", { method: "POST", body: JSON.stringify({ raw_text: $("#raw-text").value, hospital_id: $("#hospital-id").value, source_channel: "admin-console", request_id: $("#request-id").value || undefined }) }); logActivity("Request extracted", `${intake.request.group} ${intake.request.component} / ${intake.request.qty} units`); const result = await api("/match-svc/api/v1/match", { method: "POST", body: JSON.stringify(await livePayload(intake.request)) }); const view = await api(`/match-svc/api/v1/cases/${result.case.case_id}?role=${state.role}`); renderCase(view); logActivity("Case ranked", `${view.case.case_id} with ${view.case.units_from_inventory} inventory units and ${view.case.units_from_donors_remaining} donor units remaining`); showNotice("Emergency request matched. Review the reservation proposal below."); } catch (error) { showNotice(error.message, true); } finally { button.disabled = false; } });

$("#approval-actions").addEventListener("click", async (event) => { const button = event.target.closest("button[data-decision]"); if (!button || !state.caseView || state.role !== "bank_admin") return; const proposal = state.caseView.recommendations[0]; button.disabled = true; try { const decision = button.dataset.decision; const result = await api(`/match-svc/api/v1/cases/${state.caseView.case.case_id}/reservations/${proposal.rec_id}/${decision}`, { method: "POST", body: JSON.stringify({ actor: "admin-ops", role: state.role, rationale: decision === "approve" ? "Inventory confirmed for emergency fulfillment" : "Reservation declined by operations" }) }); renderCase(result); logActivity(decision === "approve" ? "Inventory reserved" : "Reservation rejected", `${proposal.rec_id} processed by admin-ops`); showNotice(decision === "approve" ? "Reservation approved and inventory is now reserved." : "Reservation rejected. Inventory remains available."); } catch (error) { showNotice(error.message, true); } finally { button.disabled = false; } });

$("#patient-request-form").addEventListener("submit", async (event) => { event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true; try { const intake = await api("/intake-svc/api/v1/extract", { method: "POST", body: JSON.stringify({ raw_text: $("#patient-raw-text").value, hospital_id: $("#patient-hospital-id").value, source_channel: "patient-web" }) }); state.patientDraft = intake.request; renderPatientSummary(intake.request); $("#patient-confirmation").hidden = false; showNotice("Please confirm the request details before BloodNet starts matching."); } catch (error) { showNotice(error.message, true); } finally { button.disabled = false; } });
$("#patient-edit").addEventListener("click", () => { $("#patient-confirmation").hidden = true; $("#patient-raw-text").focus(); });
$("#patient-confirm").addEventListener("click", async (event) => { if (!state.patientDraft) return; const button = event.currentTarget; button.disabled = true; try { const result = await api("/match-svc/api/v1/match", { method: "POST", body: JSON.stringify(await livePayload(state.patientDraft)) }); const view = await api(`/match-svc/api/v1/cases/${result.case.case_id}?role=patient`); renderPatientTracking(view); showNotice("Request confirmed. BloodNet is now coordinating fulfillment."); } catch (error) { showNotice(error.message, true); } finally { button.disabled = false; } });
$("#role-select").addEventListener("change", (event) => { state.role = event.target.value; document.body.classList.toggle("patient-mode", state.role === "patient"); $("#patient-view").hidden = state.role !== "patient"; $(".workspace").hidden = state.role === "patient"; $(".activity-panel").hidden = state.role === "patient"; loadCases().catch((error) => showNotice(error.message, true)); });
$("#refresh-button").addEventListener("click", () => loadCases().catch((error) => showNotice(error.message, true)));
api("/health").then(() => { $("#connection-label").textContent = "API connected"; }).catch(() => { $("#connection-label").textContent = "API unavailable"; $(".status-dot").classList.add("offline"); });
loadCases().catch(() => {});

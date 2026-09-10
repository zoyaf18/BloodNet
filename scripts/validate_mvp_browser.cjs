// Actual candidate browser -> Firebase -> gateway -> Cloud Run -> Cloud SQL.
// No credentials, tokens, cookies, screenshots or user profiles are persisted.
const { chromium } = require('../web/app/node_modules/playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '..');
const origin = JSON.parse(fs.readFileSync(`${root}/docs/mvp-candidate-hosting.json`)).origin;
const base = JSON.parse(fs.readFileSync(`${root}/docs/mvp-candidate-gateway.json`)).url;
assert(origin.includes('--mvp-review-') && base.includes('bloodnet-mvp-review-'), 'Writes are restricted to the candidate');
const config = Object.fromEntries(fs.readFileSync(`${root}/web/app/.env.production`, 'utf8').split(/\r?\n/).filter(line => line.includes('=') && !line.startsWith('#')).map(line => { const n = line.indexOf('='); return [line.slice(0,n), line.slice(n+1).replace(/^['"]|['"]$/g,'')]; }));
const key = config.VITE_BLOODNET_GATEWAY_API_KEY || config.VITE_IDENTITY_PLATFORM_API_KEY;
const accounts = process.env.BLOODNET_SEEDED_DONORS === 'true'
  ? (() => {
      const baseAccounts = JSON.parse(fs.readFileSync(`${root}/.ui-check-credentials.json`));
      const imported = JSON.parse(fs.readFileSync(`${root}/docs/candidate-firebase-import.json`)).users;
      return {
        bank: baseAccounts.bank_admin,
        hospital: baseAccounts.hospital_coordinator,
        regional: baseAccounts.regional_admin,
        auditor: baseAccounts.auditor,
        donor1: { email: imported[6].email, password: baseAccounts.donor.password },
        donor2: { email: imported[7].email, password: baseAccounts.donor.password },
      };
    })()
  : JSON.parse(process.env.BLOODNET_SCENARIO_ACCOUNTS);
delete process.env.BLOODNET_SCENARIO_ACCOUNTS;
const phase = process.env.BLOODNET_MVP_PHASE || 'setup';
const statePath = `${root}/docs/mvp-browser-scenario-state.json`;
const state = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath)) : { scenario: 'MVP-REVIEW-20260910', units: ['MVP-REVIEW-20260910-U1','MVP-REVIEW-20260910-U2'] };
const report = { phase, origin, base, at: new Date().toISOString(), steps: [], api: [], failures: [] };
let browser;
const pages = [];
async function login(role) {
  const context = await browser.newContext({ timezoneId: 'Asia/Kolkata' });
  const page = await context.newPage(); pages.push(page);
  page.on('pageerror', error => report.failures.push({role, type:'pageerror', message:error.message.slice(0,200)}));
  await page.goto(origin, {waitUntil:'domcontentloaded'});
  await page.locator('input[type=email]').first().fill(accounts[role].email);
  await page.locator('input[type=password]').first().fill(accounts[role].password);
  await page.locator('form').filter({has:page.locator('input[type=password]')}).locator('button[type=submit]').click();
  await page.getByRole('button',{name:'Sign out',exact:true}).waitFor({timeout:60000});
  await page.getByText('Validation workspace:',{exact:false}).waitFor();
  report.steps.push({name:`${role}_browser_login`,passed:true});
  return page;
}
async function api(page, path, method='GET', body) {
  const result = await page.evaluate(async ({base,key,path,method,body}) => {
    const token = sessionStorage.getItem('bloodnet_access_token');
    const response = await fetch(base+path+(path.includes('?')?'&':'?')+'key='+encodeURIComponent(key), {
      method, headers:{Authorization:'Bearer '+token,...(body === undefined ? {} : {'Content-Type':'application/json'})},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return {status:response.status, body:await response.json().catch(()=>({}))};
  }, {base,key,path,method,body});
  report.api.push({method,path,status:result.status});
  return result;
}
async function reload(page) { await page.reload({waitUntil:'domcontentloaded'}); await page.getByRole('button',{name:'Sign out',exact:true}).waitFor({timeout:45000}); }
async function clickAndResponse(page, name, path) {
  const response = page.waitForResponse(r=>r.url().includes(path)&&r.request().method()==='POST',{timeout:45000});
  response.catch(()=>{});
  await page.getByRole('button',{name,exact:true}).click();
  const result = await response;
  const body = await result.json().catch(()=>({}));
  report.api.push({method:'POST',path,status:result.status()});
  assert.equal(result.status(),200, `${path}: ${JSON.stringify(body).slice(0,400)}`);
  return body;
}
(async()=>{
  browser = await chromium.launch({headless:true});
  try {
    if (phase === 'setup') {
      const bank = await login('bank');
      await bank.getByRole('button',{name:'Inventory',exact:true}).click();
      await bank.getByText('Record a blood unit',{exact:true}).click();
      const inventory = await api(bank,'/match-svc/api/v1/inventory'); assert.equal(inventory.status,200);
      for(const unit of state.units) {
        if(inventory.body.units.some(item=>item.unit_id===unit)) continue;
        await bank.getByLabel('Unit barcode',{exact:true}).fill(unit);
        await bank.getByLabel('Unit blood group').selectOption('O+');
        await bank.getByLabel('Unit component').selectOption('RBC');
        await bank.getByLabel('Collected at',{exact:true}).fill('2026-09-09T12:00');
        await bank.getByLabel('Expires at',{exact:true}).fill('2026-09-30T12:00');
        await clickAndResponse(bank,'Record unit','/integrations/blood-bank/inventory');
      }
      await reload(bank); await bank.getByRole('button',{name:'Inventory',exact:true}).click();
      await bank.getByRole('button',{name:'Inventory (2)',exact:true}).waitFor();
      report.steps.push({name:'bank_records_two_units_and_reload_preserves_stock',passed:true});
      const hospital = await login('hospital');
      if (state.case_id) {
        const old = await api(hospital,`/match-svc/api/v1/cases/${state.case_id}`);
        if (old.status === 200 && old.body.case.outcome !== 'cancelled') {
          const cancelled = await api(hospital,`/match-svc/api/v1/cases/${state.case_id}/cancel`,'POST',{reason:'TEST ONLY: replace unapproved scenario after authorized donor DOB correction'});
          assert.equal(cancelled.status,200);
          state.cancelled_case_id = state.case_id;
          report.steps.push({name:'unapproved_case_cancellation_preserves_inventory',passed:true});
        }
      }
      const context = await api(hospital,'/match-svc/api/v1/hospital/context'); assert.equal(context.status,200);
      assert.equal(context.body.units.filter(unit=>state.units.includes(unit.unit_id)).length,2);
      await hospital.getByRole('button',{name:'Hospital portal',exact:true}).click();
      await hospital.getByLabel('Blood group').selectOption('O+');
      await hospital.getByLabel('Component').selectOption('RBC');
      await hospital.getByLabel('Quantity',{exact:true}).fill('4');
      await hospital.getByLabel('Urgency').selectOption('Critical');
      await hospital.getByLabel('Required by',{exact:true}).fill('2026-09-11T23:00');
      await hospital.getByLabel('Emergency details',{exact:true}).fill('');
      await hospital.getByRole('button',{name:'Extract and review',exact:true}).click();
      const match = await clickAndResponse(hospital,'Confirm and create case','/api/v1/match');
      assert.equal(match.case.units_from_inventory,2);
      assert.equal(match.case.units_from_donors_remaining,2);
      assert.equal(match.ranked_donors.length,2);
      state.case_id = match.case.case_id; state.request_id = match.case.request_id; state.rec_id = match.recommendation.rec_id;
      report.steps.push({name:'hospital_four_unit_request_two_inventory_two_shortfall',passed:true,ranked_donors:match.ranked_donors.length});
      const regional = await login('regional');
      const deliveries = await api(regional,'/match-svc/api/v1/notifications/delivery-operations');
      assert.equal(deliveries.status,200); assert.equal(deliveries.body.deliveries.length,0);
      const auditor = await login('auditor');
      const denied = await api(auditor,`/match-svc/api/v1/cases/${state.case_id}/reservations/${state.rec_id}/approve`,'POST',{});
      assert.equal(denied.status,403);
      report.steps.push({name:'no_delivery_before_approval_and_auditor_cannot_approve',passed:true});
      await reload(hospital);
      const persisted = await api(hospital,`/match-svc/api/v1/cases/${state.case_id}`); assert.equal(persisted.status,200);
      assert.equal(persisted.body.case.units_from_donors_remaining,2);
      fs.writeFileSync(statePath,JSON.stringify(state,null,2));
    } else if (phase === 'approve') {
      assert(!state.approved, 'Approval is not repeated: only two test emails are authorized');
      const bank = await login('bank');
      await bank.getByRole('button',{name:'Inventory',exact:true}).click();
      await bank.getByRole('button',{name:/Approval Queue/}).click();
      await bank.locator('.queue-item').filter({hasText:state.rec_id}).getByRole('button',{name:'Review and approve',exact:true}).click();
      const response = bank.waitForResponse(r=>r.request().method()==='POST' && r.url().includes('/approve'),{timeout:60000});
      await bank.getByRole('button',{name:'Approve recommendation',exact:true}).click();
      const result = await response;
      assert.equal(result.status(),200,JSON.stringify(await result.json()).slice(0,400));
      state.approved = true;
      fs.writeFileSync(statePath,JSON.stringify(state,null,2));
      report.steps.push({name:'bank_human_approval_in_browser',passed:true});
      const reservations = await api(bank,'/match-svc/api/v1/reservations');
      const own = reservations.body.reservations.filter(item=>item.case_id===state.case_id);
      assert.equal(own.length,1); state.reservation_id=own[0].reservation_id;
      assert.equal(own[0].unit_ids.length,2);
      const regional = await login('regional');
      let deliveries;
      for(let attempt=0;attempt<24;attempt++) {
        deliveries=await api(regional,'/match-svc/api/v1/notifications/delivery-operations');
        if(deliveries.body.deliveries.length===2 && deliveries.body.deliveries.every(item=>item.provider_status==='accepted'||item.provider_status==='delivered')) break;
        await regional.waitForTimeout(5000);
      }
      report.delivery_states=deliveries.body.deliveries.map(item=>({status:item.status,provider_status:item.provider_status}));
      assert.equal(deliveries.body.deliveries.length,2);
      assert(deliveries.body.deliveries.every(item=>item.provider_status==='accepted'||item.provider_status==='delivered'),'Provider acceptance not verified');
      report.steps.push({name:'two_real_smtp_deliveries_only_after_human_approval',passed:true});
    } else if (phase === 'delivery') {
      const regional=await login('regional');
      const deliveries=await api(regional,'/match-svc/api/v1/notifications/delivery-operations');
      report.delivery_states=deliveries.body.deliveries.map(item=>({status:item.status,delivery_status:item.delivery_status,attempts:item.attempts,terminal_failure:item.terminal_failure}));
      assert.equal(deliveries.body.deliveries.length,2);
      assert(deliveries.body.deliveries.every(item=>['accepted','delivered'].includes(item.delivery_status)),'Provider acceptance not verified');
      report.steps.push({name:'two_real_smtp_provider_acceptances',passed:true});
    } else if (phase === 'responses') {
      const hospital = await login('hospital');
      for (const [index,role] of ['donor1','donor2'].entries()) {
        const donor = await login(role);
        const opportunities=await api(donor,'/swarm-svc/api/v1/opportunities');
        const own=opportunities.body.opportunities.filter(item=>item.case_id===state.case_id);
        assert.equal(own.length,1);
        if(own[0].status==='pending') await clickAndResponse(donor,'Accept request',`/outreach/${own[0].outreach_id}/response`);
        await reload(donor);
        const persisted=await api(donor,'/swarm-svc/api/v1/opportunities');
        assert.equal(persisted.body.opportunities.find(item=>item.outreach_id===own[0].outreach_id).status,'accepted');
        const current=await api(hospital,`/match-svc/api/v1/cases/${state.case_id}`);
        assert.equal(current.body.case.units_from_donors_fulfilled,index+1);
        assert.notEqual(current.body.case.outcome,'fulfilled');
        report.steps.push({name:`${role}_browser_acceptance_persists_without_premature_closure`,passed:true});
      }
    } else if (phase === 'receipts') {
      const bank = await login('bank');
      await bank.getByRole('button',{name:'Inventory',exact:true}).click();
      await bank.getByPlaceholder('Reservation ID',{exact:true}).fill(state.reservation_id);
      await clickAndResponse(bank,'Issue units','/consume');
      const hospital=await login('hospital');
      await hospital.getByRole('button',{name:'Hospital portal',exact:true}).click();
      let card=hospital.locator('.active-case').filter({hasText:state.case_id});
      for(const donors of [0,2]) {
        await card.getByLabel('Inventory units received',{exact:true}).fill('2');
        await card.getByLabel('Donor units received',{exact:true}).fill(String(donors));
        await card.getByLabel('Receipt reference',{exact:true}).fill(`TEST ONLY simulated receipt ${state.scenario} ${donors}`);
        const response=hospital.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes(`/cases/${state.case_id}/outcomes`));
        await card.getByRole('button',{name:'Record received units',exact:true}).click();
        assert.equal((await response).status(),200);
        await reload(hospital);
        await hospital.getByRole('button',{name:'Hospital portal',exact:true}).click();
        card=hospital.locator('.active-case').filter({hasText:state.case_id});
        const current=await api(hospital,`/match-svc/api/v1/cases/${state.case_id}`);
        assert.equal(current.body.case.confirmed_inventory_units,2);
        assert.equal(current.body.case.confirmed_donor_units,donors);
        if(donors===0) assert(await card.getByRole('button',{name:'Confirm fulfillment and close',exact:true}).isDisabled());
        report.steps.push({name:`hospital_cumulative_receipt_${2+donors}_of_4_persists`,passed:true});
      }
      const close=hospital.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes(`/cases/${state.case_id}/fulfill`));
      await card.getByRole('button',{name:'Confirm fulfillment and close',exact:true}).click();
      assert.equal((await close).status(),200);
      state.closed=true;
      report.steps.push({name:'hospital_explicit_closure_after_four_confirmed_units',passed:true});
    } else if (phase === 'scope') {
      const hospital=await login('hospital');
      const context=await api(hospital,'/match-svc/api/v1/hospital/context');
      assert.equal(context.status,200);
      const request={request_id:'SCOPE-'+require('node:crypto').randomUUID(),hospital_id:context.body.hospital.hospital_id,
        region:context.body.hospital.region,group:'O+',component:'RBC',qty:1,urgency:'Critical',required_by:new Date(Date.now()+86400000).toISOString()};
      const result=await api(hospital,'/api/v1/match','POST',{request,hospital:context.body.hospital,banks:context.body.banks,units:context.body.units,donors:[],eligible_donor_ids:[]});
      assert.equal(result.status,200);
      assert.equal(result.body.ranked_donors.length,0,'Excluded donors entered the match');
      const cancelled=await api(hospital,`/match-svc/api/v1/cases/${result.body.case.case_id}/cancel`,'POST',{reason:'TEST ONLY outreach exclusion verification complete'});
      assert.equal(cancelled.status,200);
      report.steps.push({name:`cloud_matching_excludes_${process.env.BLOODNET_SCOPE_REASON}`,passed:true});
    } else if (phase === 'persistence') {
      const hospital=await login('hospital');
      const current=await api(hospital,`/match-svc/api/v1/cases/${state.case_id}`);
      assert.equal(current.body.case.outcome,'fulfilled');
      assert.equal(current.body.case.confirmed_inventory_units,2);
      assert.equal(current.body.case.confirmed_donor_units,2);
      const bank=await login('bank');
      const stock=await api(bank,'/match-svc/api/v1/inventory');
      assert(stock.body.units.filter(item=>state.units.includes(item.unit_id)).every(item=>item.status==='issued'));
      const auditor=await login('auditor');
      const audit=await api(auditor,`/match-svc/api/v1/cases/${state.case_id}`);
      assert.equal(audit.status,200);
      report.audit_fields=Object.keys(audit.body);
      report.steps.push({name:'closed_case_and_issued_inventory_survive_new_login_after_restart',passed:true});
    } else {
      throw new Error('Phase is not implemented yet');
    }
    report.passed = true;
  } catch(error) {
    report.passed = false; report.error = String(error.message).slice(0,600);
    report.visible_buttons = await Promise.all(pages.map(page=>page.getByRole('button').allTextContents().then(items=>items.map(item=>item.trim()).slice(0,30)).catch(()=>[])));
  } finally {
    if (state.case_id) fs.writeFileSync(statePath,JSON.stringify(state,null,2));
    await browser.close();
    fs.writeFileSync(`${root}/docs/mvp-browser-${phase}.json`,JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
    if(!report.passed) process.exitCode=1;
  }
})();

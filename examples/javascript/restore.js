// Deliberately stale state for the isolated agent workflow trial.
const assert = require('node:assert/strict');
const state = {active: null, tabs: []};

async function restore(workspace, pendingTabs) {
  state.active = workspace;
  const tabs = await pendingTabs;
  state.tabs = tabs;
}

async function reproduce() {
  let finishOld;
  const oldTabs = new Promise(resolve => { finishOld = resolve; });
  const oldRestore = restore('old', oldTabs);
  await restore('new', Promise.resolve(['new-tab']));
  finishOld(['old-tab']);
  await oldRestore;
  assert.deepEqual(state, {active: 'new', tabs: ['new-tab']});
  console.log('restoration passed');
}

reproduce().catch(error => { console.error(error); process.exitCode = 1; });

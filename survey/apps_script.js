/**
 * Tellimations Study 1 / Study 2 — Google Apps Script Backend (one deployment per study)
 *
 * DEPLOYMENT INSTRUCTIONS:
 * 1. Create a new Google Sheets spreadsheet
 * 2. Create 3 sheets named: "state", "responses_block1", "responses_block2"
 * 3-6. Headers and state rows: run setupSheets(nSlots, perList) below (see SETUP)
 * 7. Go to Extensions > Apps Script
 * 8. Paste this code into Code.gs
 * 9. Deploy > New deployment > Web app
 *    - Execute as: Me
 *    - Who has access: Anyone
 * 10. Copy the deployment URL into CONFIG.API_BASE in app.js
 *
 * SETUP (run once from the Apps Script editor, after creating the 3 empty
 * sheets "state", "responses_block1", "responses_block2"):
 *   Study 1: setupSheets(80, 10)  -> headers + 80 slots, 8 lists
 *   Study 2: setupSheets(40, 10)  -> headers + 40 slots, 4 lists
 * Then deploy as a new web app and paste its URL into
 * data/study2/study2_config.json -> api_base.
 */
var HEADERS = {
  state: ['slot', 'list_id', 'participant_id', 'prolific_id', 'assigned_at', 'completed_at'],
  responses_block1: ['prolific_id', 'slot', 'block', 'stimulus_id', 'scene_id', 'animation_id',
    'condition', 'is_catch', 'selected_option_code', 'selected_option_text',
    'option_display_order', 'response_time_ms', 'video_play_count', 'timestamp'],
  responses_block2: ['prolific_id', 'slot', 'block', 'stimulus_id', 'scene_id', 'animation_id',
    'condition', 'likert_rating', 'pipeline_intent', 'response_time_ms', 'timestamp']
};

function setupSheets(nSlots, perList) {
  nSlots = nSlots || 40;
  perList = perList || 10;
  var ss = SpreadsheetApp.getActive();
  Object.keys(HEADERS).forEach(function (name) {
    var sheet = ss.getSheetByName(name) || ss.insertSheet(name);
    sheet.getRange(1, 1, 1, HEADERS[name].length).setValues([HEADERS[name]]).setFontWeight('bold');
    sheet.setFrozenRows(1);
  });
  var rows = [];
  for (var i = 1; i <= nSlots; i++) {
    rows.push([i, Math.ceil(i / perList), i, '', '', '']);   // slot, list_id, participant_id
  }
  var state = ss.getSheetByName('state');
  state.getRange(2, 1, rows.length, 6).setValues(rows);
}

// Kept for reference: Study 1 was set up with this (state rows only).
function setupStateSheet(nSlots, perList) {
  setupSheets(nSlots, perList);
}

function doPost(e) {
  var data = JSON.parse(e.postData.contents);
  var action = data.action;
  var result;

  try {
    switch (action) {
      case 'assign':
        result = handleAssign(data);
        break;
      case 'respond':
        result = handleRespond(data);
        break;
      case 'complete':
        result = handleComplete(data);
        break;
      default:
        result = { error: 'Unknown action: ' + action };
    }
  } catch (err) {
    result = { error: err.toString() };
  }

  return ContentService.createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Atomic participant assignment with lock.
 * If prolific_id already assigned, returns existing slot (idempotent for page refreshes).
 */
function handleAssign(data) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);

  try {
    var sheet = SpreadsheetApp.getActive().getSheetByName('state');
    var values = sheet.getDataRange().getValues();
    var prolificId = data.prolific_id;

    // Check if already assigned (idempotent)
    for (var i = 1; i < values.length; i++) {
      if (values[i][3] === prolificId) {
        lock.releaseLock();
        return {
          slot: values[i][0],
          list_id: values[i][1],
          participant_id: values[i][2]
        };
      }
    }

    // Find first unassigned slot
    for (var i = 1; i < values.length; i++) {
      if (!values[i][3]) {  // prolific_id column (D) is empty
        sheet.getRange(i + 1, 4).setValue(prolificId);
        sheet.getRange(i + 1, 5).setValue(new Date().toISOString());
        lock.releaseLock();
        return {
          slot: values[i][0],
          list_id: values[i][1],
          participant_id: values[i][2]
        };
      }
    }

    lock.releaseLock();
    return { error: 'full' };

  } catch (err) {
    lock.releaseLock();
    throw err;
  }
}

/**
 * Append a response row to the appropriate sheet.
 */
function handleRespond(data) {
  var sheetName = data.block === 1 ? 'responses_block1' : 'responses_block2';
  var sheet = SpreadsheetApp.getActive().getSheetByName(sheetName);

  if (data.block === 1) {
    sheet.appendRow([
      data.prolific_id,
      data.slot,
      data.block,
      data.stimulus_id,
      data.scene_id,
      data.animation_id,
      data.condition,
      data.is_catch,
      data.selected_option_code,
      data.selected_option_text,
      JSON.stringify(data.option_display_order),
      data.response_time_ms,
      data.video_play_count,
      data.timestamp
    ]);
  } else {
    sheet.appendRow([
      data.prolific_id,
      data.slot,
      data.block,
      data.stimulus_id,
      data.scene_id,
      data.animation_id,
      data.condition,
      data.likert_rating,
      data.pipeline_intent,
      data.response_time_ms,
      data.timestamp
    ]);
  }

  return { ok: true };
}

/**
 * Mark a participant slot as completed.
 */
function handleComplete(data) {
  var sheet = SpreadsheetApp.getActive().getSheetByName('state');
  var values = sheet.getDataRange().getValues();

  for (var i = 1; i < values.length; i++) {
    if (values[i][0] === data.slot) {
      sheet.getRange(i + 1, 6).setValue(new Date().toISOString());
      return { ok: true };
    }
  }

  return { error: 'Slot not found: ' + data.slot };
}

/**
 * Two Google Forms for the blind re-coding of resolutions (one per coder).
 *
 * Reads resolution_items.json (written by build_resolution_forms.py) and the scene images (A_1.jpg ... D_5.jpg)
 * from the Drive folder RES_FOLDER_ID. Each page shows one scene and several items about it. An item is one
 * grid: what the child said, the gap found by the tool and what the child said next, then two statements to
 * answer with Yes or No (gap addressed, guess of the condition). A Google Form holds 1000 elements at most,
 * which is why an item is a single element and why items share the image of their page.
 *
 * Run buildCoderA / buildCoderB. A form is built in passes shorter than the 6-minute limit of Apps Script:
 * run the same function again while the log says "Run again". rebuildCoderA / rebuildCoderB build a form again
 * from the most recent resolution_items.json of the folder and keep its link. resetCoderA / resetCoderB empty a
 * form and build it again whatever its state. resolutionLinks shows the state
 * and the links; resolutionCheck prints what build_resolution_forms.py --check prints.
 *
 * All names start with res / RES_ so that this file can sit in the same project as misl_forms.gs.
 */

var RES_FOLDER_ID = '1Iw7GoZTAI3Lp3Dnh3DNVk7vIE1DQ7SdQ';
var RES_FILE = 'resolution_items.json';
var RES_LIMIT_MS = 4.5 * 60 * 1000;
var RES_YES_NO = ['Yes', 'No'];
var RES_ROWS = ['The child addressed the gap in what they said next', 'My guess: this story was told with animations'];

/** Builds (or resumes building) the form of coder A. */
function buildCoderA() { resBuild_('A'); }
/** Builds (or resumes building) the form of coder B. */
function buildCoderB() { resBuild_('B'); }
/** Rebuilds the form of coder A from the current resolution_items.json, keeping its link. */
function rebuildCoderA() { resRestart_('A'); }
/** Rebuilds the form of coder B from the current resolution_items.json, keeping its link. */
function rebuildCoderB() { resRestart_('B'); }
/** Empties the form of coder A and builds it again whatever its state (after a run that went wrong). */
function resetCoderA() { resReset_('A'); }
/** Empties the form of coder B and builds it again whatever its state (after a run that went wrong). */
function resetCoderB() { resReset_('B'); }

/** Logs, for each coder, build state, item count and the answer and edit URLs of the form. */
function resolutionLinks() {
  var props = PropertiesService.getScriptProperties();
  var out = [];
  ['A', 'B'].forEach(function (c) {
    var id = props.getProperty('res_form_' + c);
    if (!id) { out.push('coder ' + c + ': not built'); return; }
    var form = FormApp.openById(id);
    out.push('coder ' + c + ' | state ' + props.getProperty('res_next_' + c) + ' | items ' + form.getItems().length +
      ' | answer: ' + form.getPublishedUrl() + ' | edit: ' + form.getEditUrl());
  });
  Logger.log(out.join('\n'));
}

/**
 * Prints, for each form, its number of elements and its coding items in order as id:checksum, where the checksum
 * covers the three texts shown. build_resolution_forms.py --check prints the same line from resolution_items.json.
 */
function resolutionCheck() {
  var props = PropertiesService.getScriptProperties();
  var out = [];
  ['A', 'B'].forEach(function (c) {
    var id = props.getProperty('res_form_' + c);
    if (!id) { out.push('coder ' + c + ': not built'); return; }
    var items = FormApp.openById(id).getItems();
    var parts = [];
    for (var k = 0; k < items.length; k++) {
      if (items[k].getType() !== FormApp.ItemType.GRID) continue;
      var text = items[k].getHelpText(), h = 7;
      for (var i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) % 1000000007;
      parts.push(items[k].getTitle().replace('Item ', '') + ':' + h);
    }
    out.push('coder ' + c + ' | ' + items.length + ' elements | ' + parts.join(' '));
  });
  Logger.log(out.join('\n'));
}

/** The most recently modified file of that name: a new file dropped next to the old one wins. */
function resLatest_(folder, name) {
  var files = folder.getFilesByName(name), best = null;
  while (files.hasNext()) {
    var f = files.next();
    if (!best || f.getLastUpdated() > best.getLastUpdated()) best = f;
  }
  if (!best) throw new Error(name + ' not found in the Drive folder');
  return best;
}

/** Starts a rebuild of a finished form; run again while a rebuild is under way, it continues it. */
function resRestart_(coder) {
  var props = PropertiesService.getScriptProperties();
  var state = props.getProperty('res_next_' + coder);
  if (state === 'done' || state === null) props.setProperty('res_next_' + coder, 'clear');
  resBuild_(coder);
}

/** Marks a form as to be emptied, then builds it. */
function resReset_(coder) {
  PropertiesService.getScriptProperties().setProperty('res_next_' + coder, 'clear');
  resBuild_(coder);
}

/** Runs one pass of resBuildPass_ unless another run is already building the same form. */
function resBuild_(coder) {
  var start = Date.now();
  var props = PropertiesService.getScriptProperties();
  var busy = Number(props.getProperty('res_busy_' + coder) || 0);
  if (start - busy < 6 * 60 * 1000) {
    Logger.log('Coder ' + coder + ': another run is building this form. Wait until it has finished.');
    return;
  }
  props.setProperty('res_busy_' + coder, String(start));
  try { resBuildPass_(coder, start, props); } finally { props.deleteProperty('res_busy_' + coder); }
}

/**
 * Builds the form of a coder, page by page; stops before RES_LIMIT_MS and records its progress in script
 * properties (res_next_X = next page to build, res_form_X) so that a rerun continues.
 */
function resBuildPass_(coder, start, props) {
  var folder = DriveApp.getFolderById(RES_FOLDER_ID);
  var pages = JSON.parse(resLatest_(folder, RES_FILE).getBlob().getDataAsString('UTF-8')).coders[coder];
  var count = 0;
  pages.forEach(function (p) { count += p.items.length; });
  var state = props.getProperty('res_next_' + coder);
  if (state === 'done') {
    Logger.log('Coder ' + coder + ': already built. Run rebuildCoder' + coder + ' to build it again from ' + RES_FILE + '.');
    return;
  }
  var formId = props.getProperty('res_form_' + coder);
  var form;
  if (!formId) {
    form = FormApp.create('Resolution coding, Tellimation study (coder ' + coder + ')');
    DriveApp.getFileById(form.getId()).moveTo(folder);
    props.setProperty('res_form_' + coder, form.getId());
    state = 'clear';
  } else {
    form = FormApp.openById(formId);
  }
  if (state === null || state === 'clear') {
    var old = form.getItems();
    if (old.length) Logger.log('Coder ' + coder + ': removing ' + old.length + ' elements.');
    for (var k = old.length - 1; k >= 0; k--) {
      if (Date.now() - start > RES_LIMIT_MS) {
        props.setProperty('res_next_' + coder, 'clear');
        Logger.log('Coder ' + coder + ': still removing the old content (' + (k + 1) + ' elements left). Run again.');
        return;
      }
      form.deleteItem(old[k]);
    }
    resIntro_(form, count, pages.length);
    state = '0';
    props.setProperty('res_next_' + coder, state);
  }
  var blobs = {};
  var image = function (name) {
    if (!blobs[name]) blobs[name] = folder.getFilesByName(name).next().getBlob();
    return blobs[name];
  };
  for (var i = Number(state); i < pages.length; i++) {
    if (Date.now() - start > RES_LIMIT_MS) {
      Logger.log('Coder ' + coder + ': ' + i + ' of ' + pages.length + ' pages built. Run again.');
      return;
    }
    resPage_(form, pages[i], i + 1, pages.length, image);
    props.setProperty('res_next_' + coder, String(i + 1));
  }
  props.setProperty('res_next_' + coder, 'done');
  Logger.log('Coder ' + coder + ': done, ' + count + ' items on ' + pages.length + ' pages. ' + form.getPublishedUrl());
}

/** Sets the form options and adds the five sections of the first page. */
function resIntro_(form, n, pages) {
  form.setDescription('Please read this page before you start coding.')
    .setProgressBar(true)
    .setCollectEmail(false)
    .setAllowResponseEdits(true)
    .setShowLinkToRespondAgain(false)
    .setConfirmationMessage('Thank you. Your coding has been recorded.');
  var section = function (title, lines) {
    form.addSectionHeaderItem().setTitle(title).setHelpText(lines.join('\n'));
  };
  section('What you will do', [
    'You will code ' + n + ' items. Each item comes from a story that a child with language difficulties told aloud ' +
    'with a storytelling tool, from five illustrated scenes shown one after the other.',
    'Each time the child said something about a scene, the tool looked for a gap in it: something in the illustration ' +
    'that the child had not said yet, or something the child said that did not match the illustration.',
    'For each item, you decide whether the child addressed that gap in the next thing they said about the same scene.'
  ]);
  section('What you will see', [
    'The form has ' + pages + ' pages. Each page shows the illustration of one scene, then several items about that ' +
    'scene. The items of a page come from different children and different stories. Each item shows three texts:',
    '• What the child said: one recording. The child held down the space bar to record, and could record several ' +
    'times for the same scene.',
    '• The gap found by the tool: the tool\'s own note on what was missing or incorrect in what the child said. ' +
    'Some notes are worded as if the tool were speaking to the child.',
    '• What the child said next: the child\'s next recording about the same scene.',
    'Under the three texts, two statements are to be answered with Yes or No.'
  ]);
  section('First statement: the child addressed the gap', [
    '• Answer Yes when the child addressed the gap in what they said next: they added what was missing, or corrected ' +
    'what was incorrect.',
    '• Be generous: if the child made a reasonable attempt to address the gap, answer Yes.',
    '• Answer No when what the child said next does not address the gap.',
    '• Use the illustration to check what is in the scene.',
    '• Judge each item on its own, from the three texts shown for that item.'
  ]);
  section('Second statement: your guess about the animations', [
    'Some stories were told with the tool\'s animations: after the child spoke, the tool could play a short animation ' +
    'on the illustration to draw the child\'s attention to the gap. The other stories were told without animations: ' +
    'the tool found the gaps in the same way but showed nothing.',
    'Nothing in the form says which stories were told with animations. For each item, answer Yes if you think its ' +
    'story was told with animations, and No if you think it was told without. ' +
    'Your guess does not need to be right: it tells us whether the items give this information away.'
  ]);
  section('About the transcripts and the form', [
    '• The transcripts were produced automatically by a speech-recognition tool. Some words may have been distorted by the tool.',
    '• The transcripts are raw: they have not been corrected or cleaned up.',
    '• A parent sometimes spoke during a recording, and their words can be mixed with the child\'s.',
    '• Children sometimes released the space bar too early. When this happened, the end of their sentence is cut off.',
    '• Both statements are required for every item. Please code the pages in the order in which they appear.',
    '• Each page ends with a comment box. It is optional. Please give the item code with each remark.',
    '• Your progress is saved automatically if you are signed in to a Google account. To come back later, open the ' +
    'same link while signed in to the same account. A saved draft is kept for 30 days after your last change.'
  ]);
}

/** The three texts of an item; build_resolution_forms.py builds the same string for its checksum. */
function resTexts_(item) {
  return 'WHAT THE CHILD SAID\n' + item.utterance + '\n\nTHE GAP FOUND BY THE TOOL\n' + item.gap +
    '\n\nWHAT THE CHILD SAID NEXT\n' + item.next;
}

/** Adds one page: the scene image, one grid per item (three texts, two Yes/No statements) and a comment box. */
function resPage_(form, page, index, total, image) {
  form.addPageBreakItem().setTitle('Page ' + index + ' of ' + total)
    .setHelpText(page.items.length + ' items about the scene shown below.');
  form.addImageItem().setTitle('Scene').setImage(image(page.image)).setAlignment(FormApp.Alignment.CENTER).setWidth(680);
  page.items.forEach(function (item) {
    form.addGridItem().setTitle('Item ' + item.id).setHelpText(resTexts_(item))
      .setRows(RES_ROWS).setColumns(RES_YES_NO).setRequired(true);
  });
  form.addParagraphTextItem().setTitle('Page ' + index + ' · Comments')
    .setHelpText('Optional. Please give the item code with each remark, for example G012.');
}

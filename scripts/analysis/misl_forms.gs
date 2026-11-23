/**
 * MISL scoring forms, one Google Form per rater (Apps Script, script.google.com).
 *
 * Reads stories.json and the scene images (A_1.jpg ... D_5.jpg) from the Drive
 * folder FOLDER_ID; both are written by scripts/analysis/build_misl_forms.py.
 *
 * Run buildRater1, buildRater2, buildRater3. A run stops by itself before the
 * 6-minute limit of Apps Script; if the log says "run again", run the same
 * function again and it continues where it stopped.
 *
 * New transcripts: replace stories.json in the folder, then run rebuildRater1,
 * rebuildRater2, rebuildRater3. The forms keep their links. links() prints them.
 */
var FOLDER_ID = '1Iw7GoZTAI3Lp3Dnh3DNVk7vIE1DQ7SdQ';
var LIMIT_MS = 4.5 * 60 * 1000;
var SCALE = ['0', '1', '2', '3'];
var MACRO = ['Character', 'Setting', 'Initiating Event', 'Internal Response', 'Plan', 'Action/Attempt', 'Consequence'];
var MICRO = ['Coordinating Conjunctions', 'Subordinating Conjunctions', 'Mental verbs', 'Linguistic verbs',
  'Adverbs', 'Elaborated Noun Phrases', 'Grammaticality', 'Tense'];

var ABOUT_TITLE = 'About the transcripts';
var ABOUT = [
  '• The transcripts were produced automatically by a speech-recognition tool. Some words may have been distorted by the tool.',
  '• The transcripts are raw: they have not been corrected or cleaned up.',
  '• A parent sometimes spoke during a recording, and their words can be mixed with the child\'s. Score only what the child ' +
  'said: do not give credit for words that clearly come from an adult, for example a question, or an answer the adult suggests.',
  '• Children sometimes released the space bar too early. When this happened, the end of their sentence is cut off. ' +
  'Do not count a sentence that is cut off at its end as a grammatical error.',
  '• Apart from these two cases, please score the transcripts as written.'
];

var KEEP_TITLE = 'Please keep in mind';
var KEEP = [
  '• The transcripts are de-identified. Please do not share them or keep copies after the study.',
  '• If something is unclear, please write to Vincent Cavez before scoring, so that all raters get the same answer.'
];

/** Rewrites the "About the transcripts" and "Please keep in mind" sections of the three forms, without touching the stories. */
function updateAbout() {
  var props = PropertiesService.getScriptProperties();
  var texts = {};
  texts[ABOUT_TITLE] = ABOUT;
  texts[KEEP_TITLE] = KEEP;
  var out = [];
  [1, 2, 3].forEach(function (r) {
    var id = props.getProperty('form_' + r);
    if (!id) { out.push('rater ' + r + ': not built'); return; }
    var items = FormApp.openById(id).getItems(FormApp.ItemType.SECTION_HEADER);
    var done = 0;
    for (var k = 0; k < items.length; k++) {
      var lines = texts[items[k].getTitle()];
      if (lines) { items[k].setHelpText(lines.join('\n')); done++; }
    }
    out.push('rater ' + r + ': ' + done + ' sections updated');
  });
  Logger.log(out.join('\n'));
}

/** Builds (or resumes building) the form of rater 1. */
function buildRater1() { build_(1); }
/** Builds (or resumes building) the form of rater 2. */
function buildRater2() { build_(2); }
/** Builds (or resumes building) the form of rater 3. */
function buildRater3() { build_(3); }
/** Rebuilds the form of rater 1 from the current stories.json, keeping its link. */
function rebuildRater1() { restart_(1); }
/** Rebuilds the form of rater 2 from the current stories.json, keeping its link. */
function rebuildRater2() { restart_(2); }
/** Rebuilds the form of rater 3 from the current stories.json, keeping its link. */
function rebuildRater3() { restart_(3); }

/** Logs and returns, for each rater, build state, item count and the answer and edit URLs of the form. */
function links() {
  var props = PropertiesService.getScriptProperties();
  var out = [];
  [1, 2, 3].forEach(function (r) {
    var id = props.getProperty('form_' + r);
    if (!id) { out.push('rater ' + r + ': not built'); return; }
    var form = FormApp.openById(id);
    out.push('rater ' + r + ' | state ' + props.getProperty('next_' + r) + ' | items ' + form.getItems().length +
      ' | answer: ' + form.getPublishedUrl() + ' | edit: ' + form.getEditUrl());
  });
  Logger.log(out.join('\n'));
  return out.join('\n');
}

/**
 * Prints, for each form, its number of items and its stories in order as code:checksum, where the checksum covers
 * the transcripts shown. scripts/analysis/build_misl_forms.py --check prints the same line from stories.json.
 */
function check() {
  var props = PropertiesService.getScriptProperties();
  var out = [];
  [1, 2, 3].forEach(function (r) {
    var id = props.getProperty('form_' + r);
    if (!id) { out.push('rater ' + r + ': not built'); return; }
    var items = FormApp.openById(id).getItems();
    var codes = [], sums = {}, code = null;
    for (var k = 0; k < items.length; k++) {
      var type = items[k].getType();
      if (type === FormApp.ItemType.PAGE_BREAK) {
        code = items[k].getHelpText().replace('Story code: ', '');
        codes.push(code);
        sums[code] = 7;
      } else if (code && type === FormApp.ItemType.SECTION_HEADER) {
        var text = items[k].getHelpText() + '|';
        for (var c = 0; c < text.length; c++) sums[code] = (sums[code] * 31 + text.charCodeAt(c)) % 1000000007;
      }
    }
    out.push('rater ' + r + ' | ' + items.length + ' items | ' + codes.map(function (x) { return x + ':' + sums[x]; }).join(' '));
  });
  Logger.log(out.join('\n'));
}

/** The most recently modified file of that name: a new stories.json dropped next to the old one wins. */
function latest_(folder, name) {
  var files = folder.getFilesByName(name), best = null;
  while (files.hasNext()) {
    var f = files.next();
    if (!best || f.getLastUpdated() > best.getLastUpdated()) best = f;
  }
  if (!best) throw new Error(name + ' not found in the Drive folder');
  return best;
}

/** Starts a rebuild of a finished form; run again while a rebuild is under way, it continues it instead of starting over. */
function restart_(rater) {
  var props = PropertiesService.getScriptProperties();
  var state = props.getProperty('next_' + rater);
  if (state === 'done' || state === null) props.setProperty('next_' + rater, 'clear');
  build_(rater);
}

/**
 * Builds the form of a rater from stories.json (Drive folder), one story per page; stops before LIMIT_MS
 * and records its progress in script properties (next_N, form_N) so that a rerun continues.
 */
function build_(rater) {
  var start = Date.now();
  var props = PropertiesService.getScriptProperties();
  var folder = DriveApp.getFolderById(FOLDER_ID);
  var data = JSON.parse(latest_(folder, 'stories.json').getBlob().getDataAsString('UTF-8'));
  var stories = data.raters[String(rater)];
  var state = props.getProperty('next_' + rater);
  if (state === 'done') {
    Logger.log('Rater ' + rater + ': already built. Run rebuildRater' + rater + ' to build it again from stories.json.');
    return;
  }
  var formId = props.getProperty('form_' + rater);
  var form;
  if (!formId) {
    form = FormApp.create('MISL scoring, Tellimation study (rater ' + rater + ')');
    DriveApp.getFileById(form.getId()).moveTo(folder);
    props.setProperty('form_' + rater, form.getId());
    state = 'clear';
  } else {
    form = FormApp.openById(formId);
  }
  if (state === null || state === 'clear') {
    var items = form.getItems();
    for (var k = items.length - 1; k >= 0; k--) {
      if (Date.now() - start > LIMIT_MS) {
        props.setProperty('next_' + rater, 'clear');
        Logger.log('Rater ' + rater + ': still removing the old content. Run again.');
        return;
      }
      form.deleteItem(items[k]);
    }
    intro_(form, stories.length);
    state = '0';
    props.setProperty('next_' + rater, state);
  }
  var blobs = {};
  var image = function (name) {
    if (!blobs[name]) blobs[name] = folder.getFilesByName(name).next().getBlob();
    return blobs[name];
  };
  for (var i = Number(state); i < stories.length; i++) {
    if (Date.now() - start > LIMIT_MS) {
      Logger.log('Rater ' + rater + ': ' + i + ' of ' + stories.length + ' stories built. Run again.');
      return;
    }
    story_(form, stories[i], i + 1, stories.length, image);
    props.setProperty('next_' + rater, String(i + 1));
  }
  props.setProperty('next_' + rater, 'done');
  Logger.log('Rater ' + rater + ': done, ' + stories.length + ' stories. ' + form.getPublishedUrl());
}

/** Sets the form options and adds the introduction page sections (task, what is shown, transcripts, scoring, reminders). */
function intro_(form, n) {
  form.setDescription('Please read this page before you start scoring.')
    .setProgressBar(true)
    .setCollectEmail(false)
    .setAllowResponseEdits(true)
    .setShowLinkToRespondAgain(false)
    .setConfirmationMessage('Thank you. Your scores have been recorded.');
  var section = function (title, lines) {
    form.addSectionHeaderItem().setTitle(title).setHelpText(lines.join('\n'));
  };
  section('What you will do', [
    'You will score ' + n + ' transcripts of stories told aloud by children with language difficulties. ' +
    'Each child told the story from five illustrated scenes, shown one after the other. You score each story with the ' +
    'Monitoring Indicators of Scholarly Language (MISL) rubric (Gillam & Gillam, 2010), which you received as a separate PDF.'
  ]);
  section('What you will see for each story', [
    '• The five illustrations of the story, in order.',
    '• Under each illustration, what the child said about that scene. Each line is one recording: the child held down ' +
    'the space bar to record, and could record several times for the same scene.',
    '• Some stories were told with the tool\'s animations and some without. Nothing in the form says which.'
  ]);
  section(ABOUT_TITLE, ABOUT);
  section('How to score', [
    '1. Score the story as a whole: one set of scores per story, covering the five scenes together.',
    '2. Give each of the 15 MISL elements a score from 0 to 3, following the descriptors and notes of the rubric.',
    '3. For Grammaticality and Tense, also enter the number you counted (see below).',
    '4. Score the stories in the order in which they appear in the form.',
    '5. Add a comment on the story if you wish.',
    '6. You do not need to compute totals.',
    '',
    'Macrostructure (7): ' + MACRO.join(', ') + '.',
    'Microstructure (8): ' + MICRO.join(', ') + '.',
    'The 15 elements are named as in the rubric, and the form asks for them in this order.'
  ]);
  section('Grammaticality and Tense', [
    'Score these two elements from 0 to 3 as the rubric says. In addition, the form has two number fields per story:',
    '• Number of grammatical errors you counted in the story.',
    '• Number of tense changes you counted in the story.',
    'Please enter the actual number, including when it is higher than three.'
  ]);
  section('Using the form', [
    '• Each story is on its own page.',
    '• Your progress is saved automatically if you are signed in to a Google account. To come back later, open the ' +
    'same link while signed in to the same account.',
    '• A saved draft is kept for 30 days after your last change, so you can score over several sittings.',
    '• Each story has a comment box. You can use it for any remark on the story or on your scores, for example a ' +
    'transcript that is hard to read or a score you hesitated on.'
  ]);
  section(KEEP_TITLE, KEEP);
  compensation_(form);
}

var PAY_TITLE = 'Compensation';
var PAY_QUESTION = 'Which compensation do you prefer?';
var PAY_CHOICES = ['US Amazon gift card ($100)', 'Check payment ($100, non-PO payment)'];
var PAY_TEXT = 'To thank you for your time, you can choose between a US Amazon gift card of $100 and a check payment ' +
  'of $100 (non-PO payment). If you choose the check payment, please fill in the fields below: they are needed to issue the payment.';

/** Rewrites the compensation text and its two choices in the three forms. */
function updateCompensation() {
  var props = PropertiesService.getScriptProperties();
  var out = [];
  [1, 2, 3].forEach(function (r) {
    var id = props.getProperty('form_' + r);
    if (!id) { out.push('rater ' + r + ': not built'); return; }
    var form = FormApp.openById(id);
    var done = 0;
    form.getItems(FormApp.ItemType.SECTION_HEADER).forEach(function (it) {
      if (it.getTitle() === PAY_TITLE) { it.setHelpText(PAY_TEXT); done++; }
    });
    form.getItems(FormApp.ItemType.MULTIPLE_CHOICE).forEach(function (it) {
      if (it.getTitle() === PAY_QUESTION) { it.asMultipleChoiceItem().setChoiceValues(PAY_CHOICES); done++; }
    });
    out.push('rater ' + r + ': ' + done + ' items updated');
  });
  Logger.log(out.join('\n'));
}

/** Adds the compensation questions at the end of the form and returns them in order. */
function compensation_(form) {
  var only = 'Only if you chose the check payment.';
  return [
    form.addSectionHeaderItem().setTitle(PAY_TITLE).setHelpText(PAY_TEXT),
    form.addMultipleChoiceItem().setTitle(PAY_QUESTION).setChoiceValues(PAY_CHOICES).setRequired(true),
    form.addTextItem().setTitle('First and last name').setHelpText(only),
    form.addParagraphTextItem().setTitle('Mailing address, city, state, zip code').setHelpText(only),
    form.addMultipleChoiceItem().setTitle('Preferred payment method').setHelpText(only)
      .setChoiceValues(['Check', 'Electronic']),
    form.addTextItem().setTitle('Email address').setHelpText(only),
    form.addMultipleChoiceItem().setTitle('Citizenship / residency status').setHelpText(only)
      .setChoiceValues(['US Citizen', 'Resident Non-US Citizen', 'Non-Resident Non-US Citizen'])
  ];
}

/** Adds the compensation questions to the first page of the three forms already built (once). */
function addCompensation() {
  var props = PropertiesService.getScriptProperties();
  var out = [];
  [1, 2, 3].forEach(function (r) {
    var id = props.getProperty('form_' + r);
    if (!id) { out.push('rater ' + r + ': not built'); return; }
    var form = FormApp.openById(id);
    var items = form.getItems();
    var firstPage = -1, pay = -1;
    for (var k = 0; k < items.length; k++) {
      if (pay < 0 && items[k].getTitle() === PAY_TITLE) pay = k;
      if (firstPage < 0 && items[k].getType() === FormApp.ItemType.PAGE_BREAK) firstPage = k;
    }
    if (pay >= 0 && (firstPage < 0 || pay < firstPage)) { out.push('rater ' + r + ': already on the first page'); return; }
    var count = 7;
    if (pay < 0) { pay = items.length; count = compensation_(form).length; }
    for (var a = 0; a < count; a++) form.moveItem(pay + a, firstPage + a);
    out.push('rater ' + r + ': ' + count + ' items now at position ' + firstPage);
  });
  Logger.log(out.join('\n'));
}

/** Adds one story page: title, the five scene images with the child's transcripts, MISL macro and micro grids, error counts and comments. */
function story_(form, story, index, total, image) {
  var code = story.code;
  form.addPageBreakItem().setTitle('Story ' + index + ' of ' + total).setHelpText('Story code: ' + code);
  for (var s = 0; s < 5; s++) {
    form.addImageItem().setTitle('Scene ' + (s + 1)).setImage(image(story.story + '_' + (s + 1) + '.jpg'))
      .setAlignment(FormApp.Alignment.CENTER).setWidth(680);
    var lines = story.scenes[s] || [];
    form.addSectionHeaderItem().setTitle('What the child said about scene ' + (s + 1))
      .setHelpText(lines.length ? lines.join('\n') : '(nothing recorded for this scene)');
  }
  form.addGridItem().setTitle(code + ' · Macrostructure').setHelpText('Score each element from 0 to 3.')
    .setRows(MACRO).setColumns(SCALE).setRequired(true);
  form.addGridItem().setTitle(code + ' · Microstructure').setHelpText('Score each element from 0 to 3.')
    .setRows(MICRO).setColumns(SCALE).setRequired(true);
  var number = FormApp.createTextValidation().setHelpText('Please enter a number, 0 or more.')
    .requireNumberGreaterThanOrEqualTo(0).build();
  form.addTextItem().setTitle(code + ' · Number of grammatical errors').setHelpText('The number you counted in this story.')
    .setValidation(number).setRequired(true);
  form.addTextItem().setTitle(code + ' · Number of tense changes').setHelpText('The number you counted in this story.')
    .setValidation(number).setRequired(true);
  form.addParagraphTextItem().setTitle(code + ' · Comments').setHelpText('Optional.');
}

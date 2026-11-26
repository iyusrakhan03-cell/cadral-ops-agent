/**
 * Thistle & Pine — Gmail bridge (email channel).
 *
 * Runs inside the shop's Gmail account as a free Google Apps Script, every minute:
 *   1. new unread emails in the inbox  -> POST to the AI backend (it saves them first, then the agents answer)
 *   2. replies / reports waiting in the backend's outbox -> sent from this Gmail, then confirmed ("acked")
 *
 * Why a bridge? The backend runs on a free Hugging Face Space, which can only make web (HTTPS) requests —
 * Gmail's IMAP/SMTP ports are blocked there. Apps Script talks to Gmail natively and to the backend over HTTPS.
 *
 * Nothing is lost: an email is only marked read after the backend confirms it saved it, and an outgoing email is
 * only confirmed after Gmail sent it. Anything that fails is simply tried again next minute.
 *
 * Setup (5 minutes) is in channels/gmail-bridge/README.md. Script properties needed: API_URL, BRIDGE_KEY.
 */

var SENDER_NAME = 'Thistle & Pine Customer Care';
var MAX_PER_RUN = 15;

function props_() {
  var p = PropertiesService.getScriptProperties();
  var api = (p.getProperty('API_URL') || '').replace(/\/$/, '');
  var key = p.getProperty('BRIDGE_KEY') || '';
  if (!api || !key) throw new Error('Set API_URL and BRIDGE_KEY in Project Settings > Script properties');
  return { api: api, key: key };
}

function call_(method, path, body) {
  var cfg = props_();
  var opts = { method: method, muteHttpExceptions: true, headers: { 'X-Bridge-Key': cfg.key } };
  if (body) { opts.contentType = 'application/json'; opts.payload = JSON.stringify(body); }
  var r = UrlFetchApp.fetch(cfg.api + path, opts);
  var code = r.getResponseCode();
  var text = r.getContentText();
  return { ok: code >= 200 && code < 300, code: code, json: (function () { try { return JSON.parse(text); } catch (e) { return null; } })(), text: text };
}

/** Run once by hand: checks the settings and creates the every-minute trigger. */
function setup() {
  var r = call_('get', '/api/channels/ping');
  if (!r.ok) throw new Error('Backend said ' + r.code + ': ' + r.text + ' — check API_URL / BRIDGE_KEY');
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'run') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('run').timeBased().everyMinutes(1).create();
  Logger.log('Connected to the backend and scheduled every minute. Send a test email to this Gmail address.');
}

/** The every-minute job. */
function run() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;          // previous run still going
  try {
    pullInbound_();
    pushOutbox_();
  } finally {
    lock.releaseLock();
  }
}

function emailOf_(from) {
  var m = String(from).match(/<([^>]+)>/);
  return (m ? m[1] : String(from)).trim().toLowerCase();
}

function nameOf_(from) {
  var m = String(from).match(/^\s*"?([^"<]+?)"?\s*</);
  return m ? m[1].trim() : '';
}

function isAutomated_(msg, from) {
  if (/(no-?reply|mailer-daemon|postmaster|notifications?@)/i.test(from)) return true;
  var auto = (msg.getHeader('Auto-Submitted') || '').toLowerCase();
  var prec = (msg.getHeader('Precedence') || '').toLowerCase();
  return (auto && auto !== 'no') || prec === 'bulk' || prec === 'list' || prec === 'junk';
}

function pullInbound_() {
  var me = Session.getEffectiveUser().getEmail().toLowerCase();
  var threads = GmailApp.search('in:inbox is:unread newer_than:7d', 0, MAX_PER_RUN);
  for (var i = 0; i < threads.length; i++) {
    var msgs = threads[i].getMessages();
    for (var j = 0; j < msgs.length; j++) {
      var msg = msgs[j];
      if (!msg.isUnread()) continue;
      var from = emailOf_(msg.getFrom());
      if (from === me || isAutomated_(msg, from)) { msg.markRead(); continue; }
      var r = call_('post', '/api/channels/email/inbound', {
        message_id: msg.getId(), thread_id: threads[i].getId(), from: from, from_name: nameOf_(msg.getFrom()),
        subject: msg.getSubject(), body: msg.getPlainBody().slice(0, 50000)
      });
      if (!r.ok) { Logger.log('Backend not ready (' + r.code + '), will retry next minute'); return; }
      msg.markRead();                         // only after the backend confirmed it saved the email
    }
  }
}

function pushOutbox_() {
  var r = call_('get', '/api/channels/email/outbox?limit=' + MAX_PER_RUN);
  if (!r.ok || !r.json) return;
  r.json.emails.forEach(function (e) {
    try {
      if (e.reply_to_message_id) {
        // Reply inside the customer's own thread, so the conversation stays together in their inbox.
        GmailApp.getMessageById(e.reply_to_message_id).reply(e.text, { name: SENDER_NAME });
      } else {
        var opts = { name: SENDER_NAME };
        if (e.html) opts.htmlBody = e.html;
        GmailApp.sendEmail(e.to, e.subject, e.text, opts);
      }
      call_('post', '/api/channels/email/outbox/' + e.id + '/ack', { ok: true });
    } catch (err) {
      call_('post', '/api/channels/email/outbox/' + e.id + '/ack', { ok: false, error: String(err).slice(0, 1000) });
    }
  });
}

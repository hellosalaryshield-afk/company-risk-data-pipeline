/**
 * Salary Shield mail relay (Google Apps Script).
 *
 * Render's free plan blocks outgoing SMTP, so the backend sends report emails through
 * this small web app instead. It runs inside the hello.salaryshield Gmail account and
 * sends with MailApp, so mail really comes from Gmail.
 *
 * Setup: script.google.com -> New project -> paste this file -> replace SECRET_KEY ->
 * Deploy -> New deployment -> Web app (Execute as: Me, Who has access: Anyone).
 * Put the web app URL and the key in the backend's MAIL_RELAY_URL / MAIL_RELAY_KEY.
 *
 * The backend POSTs JSON:
 *   {key, to, subject, text, html, reply_to, sender_name, attachment: {name, mime_type, base64}}
 * and gets back {ok: true} or {ok: false, error: "..."}.
 */

const SECRET_KEY = 'REPLACE_WITH_THE_SHARED_KEY';

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.key !== SECRET_KEY) {
      return reply({ ok: false, error: 'bad key' });
    }
    if (!body.to || !body.subject) {
      return reply({ ok: false, error: 'to and subject are required' });
    }

    const options = {
      name: body.sender_name || 'Salary Shield',
      htmlBody: body.html || undefined,
      replyTo: body.reply_to || undefined,
    };
    if (body.attachment && body.attachment.base64) {
      options.attachments = [
        Utilities.newBlob(
          Utilities.base64Decode(body.attachment.base64),
          body.attachment.mime_type || 'application/pdf',
          body.attachment.name || 'report.pdf'
        ),
      ];
    }

    MailApp.sendEmail(body.to, body.subject, body.text || '', options);
    return reply({ ok: true, remaining_today: MailApp.getRemainingDailyQuota() });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  }
}

// Opening the URL in a browser shows this, which is an easy way to check the deployment.
function doGet() {
  return reply({ ok: true, service: 'salary-shield-mail-relay' });
}

function reply(data) {
  return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
}

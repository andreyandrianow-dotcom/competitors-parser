const SPREADSHEET_ID = '151fl2XsI_gmqPXIhFA47OZ-nSQEMBbDb2JaKNXN9be0';
const NOMENCLATURE_SHEET = 'Номенклатура';
const CONTROL_SHEET = 'ParserControl';
const PARSER_WEBHOOK_PROPERTY = 'PARSER_WEBHOOK_URL';
const PARSER_WEBHOOK_TOKEN_PROPERTY = 'PARSER_WEBHOOK_TOKEN';
const TIMEZONE = 'Europe/Moscow';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Парсер')
    .addItem('Запустить сейчас', 'runParserNow')
    .addItem('Установить ежедневный запуск 07:00', 'installDailyTrigger')
    .addItem('Показать статус', 'showParserStatus')
    .addToUi();
}

function runParserNow() {
  requestParserRun_('Apps Script menu', true);
}

function runParserDaily() {
  requestParserRun_('Apps Script daily 07:00', false);
}

function requestParserRun_(source, showAlert) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const requestedAt = formatMoscowDate_(new Date());
  const props = PropertiesService.getScriptProperties();
  const webhookUrl = props.getProperty(PARSER_WEBHOOK_PROPERTY);
  const token = props.getProperty(PARSER_WEBHOOK_TOKEN_PROPERTY);

  ensureControlSheet_(ss);
  writeControlValue_(ss, 'runRequestedAt', requestedAt);
  writeControlValue_(ss, 'runStatus', webhookUrl ? 'WEBHOOK_SENT' : 'ERROR');
  writeControlValue_(ss, 'runSource', source);

  if (!webhookUrl) {
    writeControlValue_(ss, 'lastError', 'PARSER_WEBHOOK_URL is not configured');
    if (showAlert) SpreadsheetApp.getUi().alert('Не задан PARSER_WEBHOOK_URL в Script Properties.');
    return;
  }

  try {
    const response = UrlFetchApp.fetch(webhookUrl, {
      method: 'post',
      contentType: 'application/json',
      headers: token ? {Authorization: 'Bearer ' + token} : {},
      payload: JSON.stringify({
        spreadsheetId: SPREADSHEET_ID,
        requestedAt: requestedAt,
        source: source,
      }),
      muteHttpExceptions: true,
    });
    writeControlValue_(ss, 'webhookCode', String(response.getResponseCode()));
    writeControlValue_(ss, 'webhookResponse', response.getContentText().slice(0, 1000));
    if (response.getResponseCode() >= 200 && response.getResponseCode() < 300) {
      if (showAlert) SpreadsheetApp.getUi().alert('Запуск отправлен. Статус можно проверить через меню Парсер.');
    } else {
      writeControlValue_(ss, 'runStatus', 'ERROR');
      writeControlValue_(ss, 'lastError', response.getContentText().slice(0, 1000));
      if (showAlert) SpreadsheetApp.getUi().alert('Облако вернуло ошибку: ' + response.getResponseCode());
    }
  } catch (error) {
    writeControlValue_(ss, 'runStatus', 'ERROR');
    writeControlValue_(ss, 'lastError', String(error).slice(0, 1000));
    if (showAlert) SpreadsheetApp.getUi().alert('Не удалось отправить запуск: ' + error);
  }
}

function installDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    if (trigger.getHandlerFunction() === 'runParserDaily' || trigger.getHandlerFunction() === 'runParserNow') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  ScriptApp.newTrigger('runParserDaily')
    .timeBased()
    .atHour(7)
    .nearMinute(0)
    .everyDays(1)
    .inTimezone(TIMEZONE)
    .create();

  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  ensureControlSheet_(ss);
  writeControlValue_(ss, 'dailyTrigger', '07:00 Europe/Moscow');
  writeControlValue_(ss, 'dailyTriggerInstalledAt', formatMoscowDate_(new Date()));
  SpreadsheetApp.getUi().alert('Ежедневный запуск установлен на 07:00 по Москве.');
}

function showParserStatus() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  ensureControlSheet_(ss);
  const status = readControlMap_(ss);
  SpreadsheetApp.getUi().alert(
    'Статус: ' + (status.runStatus || 'нет') + '\n' +
    'Запрошен: ' + (status.runRequestedAt || 'нет') + '\n' +
    'Последнее обновление: ' + ss.getSheetByName(NOMENCLATURE_SHEET).getRange('H1').getDisplayValue()
  );
}

function markParserFinished(updatedAt, rows, statusText) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  ensureControlSheet_(ss);
  const value = updatedAt || formatMoscowDate_(new Date());
  ss.getSheetByName(NOMENCLATURE_SHEET).getRange('H1').setValue(value);
  writeControlValue_(ss, 'runStatus', statusText || 'DONE');
  writeControlValue_(ss, 'updatedAt', value);
  writeControlValue_(ss, 'rows', String(rows || ''));
}

function ensureControlSheet_(ss) {
  let sheet = ss.getSheetByName(CONTROL_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(CONTROL_SHEET);
    sheet.hideSheet();
    sheet.getRange('A1:B1').setValues([['key', 'value']]);
  }
  return sheet;
}

function readControlMap_(ss) {
  const sheet = ensureControlSheet_(ss);
  const values = sheet.getDataRange().getValues();
  const result = {};
  values.slice(1).forEach(function(row) {
    if (row[0]) result[String(row[0])] = row[1];
  });
  return result;
}

function writeControlValue_(ss, key, value) {
  const sheet = ensureControlSheet_(ss);
  const values = sheet.getDataRange().getValues();
  for (let i = 1; i < values.length; i += 1) {
    if (values[i][0] === key) {
      sheet.getRange(i + 1, 2).setValue(value);
      return;
    }
  }
  sheet.appendRow([key, value]);
}

function formatMoscowDate_(date) {
  return Utilities.formatDate(date, TIMEZONE, 'dd.MM.yyyy HH:mm:ss');
}

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { monthGrid, validDate, shiftMonth, localTimeInput, localTimeToUTC } = require('../library_web/calendar.js');

test('calendar dates remain civil dates across leap years and month boundaries', () => {
  assert.equal(validDate('2024-02-29'), true);
  assert.equal(validDate('2026-02-29'), false);
  assert.equal(validDate('2026-02-30'), false);
  assert.equal(shiftMonth('2026-12', 1), '2027-01');
  assert.equal(shiftMonth('2026-01', -1), '2025-12');
  const grid = monthGrid('2024-02');
  assert.equal(grid.length % 7, 0);
  assert.equal(grid.filter(day => day.inMonth).length, 29);
  assert.equal(grid[0].date, '2024-01-29');
  assert.equal(grid.filter(day => day.date === '2024-02-29').length, 1);
});

test('attendance input uses company time even when the browser uses another zone', () => {
  const instant = '2026-10-26T01:20:30.456Z';
  assert.equal(localTimeInput(instant, 'Asia/Hong_Kong'), '2026-10-26T09:20:30.456');
  assert.equal(localTimeToUTC('2026-10-26T09:20:30.456', 'Asia/Hong_Kong', true), instant);
  assert.equal(localTimeToUTC('2026-10-26T00:00', 'Asia/Hong_Kong'), '2026-10-25T16:00:00.000Z');
  assert.equal(localTimeToUTC('', 'Asia/Hong_Kong'), null);
  assert.throws(() => localTimeToUTC('', 'Asia/Hong_Kong', true));
});

test('DST gaps and repeated local times are rejected instead of guessing an attendance instant', () => {
  assert.throws(() => localTimeToUTC('2026-03-08T02:30', 'America/New_York'), /不存在/);
  assert.throws(() => localTimeToUTC('2026-11-01T01:30', 'America/New_York'), /兩次/);
  assert.equal(localTimeToUTC('2026-03-08T03:30', 'America/New_York'), '2026-03-08T07:30:00.000Z');
  assert.equal(localTimeToUTC('2026-11-01T02:30', 'America/New_York'), '2026-11-01T07:30:00.000Z');
  assert.throws(() => localTimeToUTC('2026-02-30T09:00', 'Asia/Hong_Kong'));
  assert.throws(() => localTimeToUTC('2026-10-26T24:00', 'Asia/Hong_Kong'));
});

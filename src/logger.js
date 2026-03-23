'use strict';
const { createLogger, format, transports } = require('winston');
const { combine, timestamp, printf, colorize } = format;
const config = require('./config');

const logFormat = printf(({ level, message, timestamp: ts, ...meta }) => {
  const extra = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : '';
  return `${ts} [${level.padEnd(7)}] ${message}${extra}`;
});

const logger = createLogger({
  level: 'debug',
  format: combine(
    timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
    logFormat
  ),
  transports: [
    // Rotating file — all levels
    new transports.File({
      filename: config.LOG_PATH,
      maxsize: 5 * 1024 * 1024, // 5 MB
      maxFiles: 3,
      tailable: true,
    }),
    // Console — warnings+ only so Rich/blessed owns the terminal
    new transports.Console({
      level: 'warn',
      format: combine(
        colorize(),
        timestamp({ format: 'HH:mm:ss' }),
        logFormat
      ),
      silent: true, // silenced by default; dashboard owns stdout
    }),
  ],
});

module.exports = logger;

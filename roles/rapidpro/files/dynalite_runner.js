const dynalite = require('dynalite');
const path = require('path');

// Parse args: node runner.js <db_path>
const dbPath = process.argv[2] || './dynamo';
const port = 4567;
const host = '127.0.0.1'; // Force IPv4 to avoid Android PRoot EADDRINUSE on dual-stack

console.log(`Starting Dynalite with path: ${dbPath}`);

const server = dynalite({
    path: dbPath,
    createTableMs: 0,
    deleteTableMs: 0,
    updateTableMs: 0
});

server.listen(port, host, function (err) {
    if (err) {
        console.error('Failed to listen:', err);
        process.exit(1);
    }
    console.log(`Dynalite listening at http://${host}:${port}`);
});

// Handle signals for graceful shutdown (helps with zombification)
process.on('SIGTERM', () => {
    console.log('Received SIGTERM, closing...');
    server.close();
    process.exit(0);
});

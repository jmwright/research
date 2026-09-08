/* server.js -- serve the visualizer and the soil CSV.
   Usage:
     node server.js [path/to/soil_log.csv]
   Defaults to the bundled sample data. The CSV path can also be set with the
   SOIL_CSV env var, and the port with PORT (default 3000). */
const express = require("express");
const path = require("path");
const fs = require("fs");

const CSV = process.env.SOIL_CSV || process.argv[2] ||
  path.join(__dirname, "..", "sample_data", "soil_sample.csv");
const PORT = process.env.PORT || 3000;

const app = express();
app.use(express.static(path.join(__dirname, "public")));

app.get("/data.csv", (_req, res) => {
  fs.readFile(CSV, (err, buf) => {
    if (err) { res.status(404).send("CSV not found: " + CSV); return; }
    res.type("text/csv").send(buf);
  });
});

app.listen(PORT, () => {
  console.log("soil visualizer: http://localhost:" + PORT);
  console.log("serving data from: " + CSV);
});

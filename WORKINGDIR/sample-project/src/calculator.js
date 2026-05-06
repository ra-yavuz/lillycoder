// Tiny calculator. Used by other modules.

function add(a, b) {
  return a + b;
}

function subtract(a, b) {
  return a - b;
}

function multiply(a, b) {
  return a * b;
}

// BUG: doesn't guard against b === 0 — caller can crash with Infinity/NaN.
// Lilly should spot this and add a check.
function divide(a, b) {
  return a / b;
}

module.exports = { add, subtract, multiply, divide };

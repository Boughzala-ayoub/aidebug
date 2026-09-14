function addOne(value) {
  const answer = value + 1;
  return answer;
}

const known = 41;
const nested = { items: [3, 5, 8], label: "test" };
const result = addOne(known);
console.log(result, process.argv.slice(2));

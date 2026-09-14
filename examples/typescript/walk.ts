function addOne(value: number): number {
  const answer = value + 1;
  return answer;
}

const known: number = 41;
const nested = { items: [3, 5, 8], label: "test" };
const result = addOne(known);
console.log(result, nested.label);

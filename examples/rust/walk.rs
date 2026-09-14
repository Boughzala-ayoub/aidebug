#[inline(never)]
fn add_one(value: i32) -> i32 {
    let answer = value + 1;
    answer
}

fn main() {
    let known = 41;
    let nested = [3, 5, 8];
    let result = add_one(known);
    println!("{} {:?}", result, nested);
}

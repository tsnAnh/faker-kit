use std::collections::HashMap;

#[allow(dead_code)]
fn unused() {}

pub fn port(config: &HashMap<String, String>) -> u16 {
    let raw = config.get("port").unwrap();
    dbg!(raw);
    println!("port is {raw}");
    raw.parse().expect("port must be a number")
}

pub fn retry_policy() -> u32 {
    todo!()
}

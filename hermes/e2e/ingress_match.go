package main

import (
	"bufio"
	"fmt"
	"os"
	"regexp"
)

func main() {
	pattern := regexp.MustCompile(os.Args[1])
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		fmt.Printf("%v\t%s\n", pattern.MatchString(scanner.Text()), scanner.Text())
	}
}

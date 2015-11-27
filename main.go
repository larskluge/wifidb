package main

import (
	"fmt"
	"log"
	"os"

	"gopkg.in/yaml.v2"
)

type Info struct {
	Ssid     string
	Bssid    string
	Auth     string
	LinkAuth string
	Password string
	Isp      string
}

func main() {
	data := Info{}
	Airport(&data)
	data.Password = Password(data)
	data.Isp = Isp()

	str, err := yaml.Marshal(&data)
	if err != nil {
		log.Fatal("Error while marshalling: %v", err)
		os.Exit(2)
	} else {
		fmt.Printf("# Wifi %s\n\n\n\n", data.Ssid)
		fmt.Print(string(str))
	}
}

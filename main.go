package main

import (
	"fmt"
	"log"
	"os"

	"gopkg.in/yaml.v2"
)

type Info struct {
	Ssid        string
	Bssid       string
	Auth        string
	LinkAuth    string
	Password    string
	Isp         string
	City        string
	Country     string
	CountryCode string
}

func main() {
	finished := make(chan bool)

	data := Info{}
	Airport(&data)
	go func() {
		Location(&data)
		finished <- true
	}()
	data.Password = Password(data)

	<-finished

	str, err := yaml.Marshal(&data)
	if err != nil {
		log.Fatal("Error while marshalling: %v", err)
		os.Exit(2)
	} else {
		fmt.Printf("# Wifi %s\n\n\n\n", data.Ssid)
		fmt.Print(string(str))
	}
}

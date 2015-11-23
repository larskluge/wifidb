package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"regexp"

	"github.com/tmc/keyring"
	"gopkg.in/yaml.v2"
)

type Info struct {
	Ssid     string
	Bssid    string
	Auth     string
	LinkAuth string
	Isp      string
	Password string
}

func main() {
	cmd := "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/A/Resources/airport"
	out, err := exec.Command(cmd, "--getinfo").Output()
	if err != nil {
		log.Printf("Error occured while getting airport network information: %v", err)
	}
	info := string(out)
	re := regexp.MustCompile(`(?m:802.11 auth: (.*)$\s*link auth: (.*)$\s*BSSID: (.*)$\s*SSID: (.*)$)`)
	matches := re.FindStringSubmatch(info)
	if len(matches) == 0 {
		log.Fatal("Wifi info not in expected format--please raise an issue with the following data:")
		log.Fatal(info)
		os.Exit(1)
	}

	data := Info{
		Ssid:     matches[4],
		Bssid:    matches[3],
		Auth:     matches[1],
		LinkAuth: matches[2],
		Isp:      Isp(),
	}

	// Password
	//
	if data.Auth != "open" || data.LinkAuth != "none" {
		pass, err := keyring.Get("AirPort", data.Ssid)
		if err == nil {
			data.Password = pass
		} else {
			log.Fatal(err)
		}
	}

	str, err := yaml.Marshal(&data)
	if err != nil {
		log.Fatal("Error while marshalling: %v", err)
		os.Exit(2)
	} else {
		fmt.Print(string(str))
	}
}
